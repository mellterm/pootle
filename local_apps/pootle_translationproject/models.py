#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2009 Zuza Software Foundation
#
# This file is part of Pootle.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import os
import cStringIO
import gettext
import zipfile
import logging

from django.conf                   import settings
from django.db                     import models, IntegrityError
from django.db.models.signals      import post_save
from django.core.exceptions import PermissionDenied
from django.utils.translation import ugettext_lazy as _

from translate.filters import checks
from translate.search  import match, indexing
from translate.storage import versioncontrol
from translate.storage.base import ParseError
from translate.misc.lru import LRUCachingDict

from pootle.scripts                import hooks
from pootle_misc.util import getfromcache, dictsum
from pootle_misc.baseurl import l
from pootle_misc.aggregate import group_by_count, max_column
from pootle_store.util import calculate_stats
from pootle_store.models           import Store, Unit, QualityCheck, PARSED, CHECKED
from pootle_store.util             import relative_real_path, absolute_real_path
from pootle_store.util import empty_quickstats, empty_completestats

from pootle_app.lib.util           import RelatedManager
from pootle_project.models     import Project
from pootle_language.models    import Language
from pootle_app.models.directory   import Directory
from pootle_app                    import project_tree
from pootle_app.models.permissions import check_permission
from pootle_app.models.signals import post_vc_update, post_vc_commit


class TranslationProjectNonDBState(object):
    def __init__(self, parent):
        self.parent = parent
        # terminology matcher
        self.termmatcher = None
        self.termmatchermtime = None
        self._indexing_enabled = True
        self._index_initialized = False
        self.indexer = None


def create_translation_project(language, project):
    if project_tree.translation_project_should_exist(language, project):
        try:
            translation_project, created = TranslationProject.objects.get_or_create(language=language, project=project)
            project_tree.scan_translation_project_files(translation_project)
            return translation_project
        except OSError:
            return None
        except IndexError:
            return None

def scan_translation_projects():
    for language in Language.objects.iterator():
        for project in Project.objects.iterator():
            create_translation_project(language, project)

class VersionControlError(Exception):
    pass

class TranslationProject(models.Model):
    _non_db_state_cache = LRUCachingDict(settings.PARSE_POOL_SIZE, settings.PARSE_POOL_CULL_FREQUENCY)

    objects = RelatedManager()
    index_directory  = ".translation_index"
    class Meta:
        unique_together = ('language', 'project')
        db_table = 'pootle_app_translationproject'

    language   = models.ForeignKey(Language, db_index=True)
    project    = models.ForeignKey(Project,  db_index=True)
    real_path  = models.FilePathField(editable=False)
    directory  = models.OneToOneField(Directory, db_index=True, editable=False)
    pootle_path = models.CharField(max_length=255, null=False, unique=True, db_index=True, editable=False)

    def __unicode__(self):
        return self.pootle_path

    def save(self, *args, **kwargs):
        project_dir = self.project.get_real_path()
        self.abs_real_path = project_tree.get_translation_project_dir(self.language, project_dir, self.file_style, make_dirs=True)
        self.directory = self.language.directory.get_or_make_subdir(self.project.code)
        self.pootle_path = self.directory.pootle_path
        super(TranslationProject, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        directory = self.directory
        super(TranslationProject, self).delete(*args, **kwargs)
        directory.delete()

    def get_absolute_url(self):
        return l(self.pootle_path)

    fullname = property(lambda self: "%s [%s]" % (self.project.fullname, self.language.fullname))
    def _get_abs_real_path(self):
        return absolute_real_path(self.real_path)

    def _set_abs_real_path(self, value):
        self.real_path = relative_real_path(value)

    abs_real_path = property(_get_abs_real_path, _set_abs_real_path)

    def _get_treestyle(self):
        return self.project.get_treestyle()

    file_style = property(_get_treestyle)

    def _get_checker(self):
        checkerclasses = [checks.projectcheckers.get(self.project.checkstyle,
                                                     checks.StandardChecker),
                          checks.StandardUnitChecker]
        return checks.TeeChecker(checkerclasses=checkerclasses,
                                 errorhandler=self.filtererrorhandler,
                                 languagecode=self.language.code)

    checker = property(_get_checker)

    def filtererrorhandler(self, functionname, str1, str2, e):
        logging.error("error in filter %s: %r, %r, %s", functionname, str1, str2, e)
        return False

    def _get_non_db_state(self):
        if not hasattr(self, "_non_db_state"):
            try:
                self._non_db_state = self._non_db_state_cache[self.id]
            except KeyError:
                self._non_db_state = TranslationProjectNonDBState(self)
                self._non_db_state_cache[self.id] = TranslationProjectNonDBState(self)

        return self._non_db_state

    non_db_state = property(_get_non_db_state)

    def update(self, conservative=True):
        """update all stores to reflect state on disk"""
        for store in self.stores.exclude(file='').filter(state__gte=PARSED).iterator():
            store.update(update_translation=True, update_structure=not conservative, conservative=conservative)

    def sync(self, conservative=True):
        """sync unsaved work on all stores to disk"""
        for store in self.stores.exclude(file='').filter(state__gte=PARSED).iterator():
            store.sync(update_translation=True, update_structure=not conservative, conservative=conservative, create=False)

    @getfromcache
    def get_mtime(self):
        return max_column(Unit.objects.filter(store__translation_project=self), 'mtime', None)

    def require_units(self):
        """makes sure all stores are parsed"""
        errors = 0
        for store in self.stores.filter(state__lt=PARSED).iterator():
            try:
                store.require_units()
            except IntegrityError:
                logging.info("Duplicate IDs in %s", store.abs_real_path)
                errors += 1
            except ParseError, e:
                logging.info("Failed to parse %s\n%s", store.abs_real_path, e)
                errors += 1
            except (IOError, OSError), e:
                logging.info("Can't access %s\n%s", store.abs_real_path, e)
                errors += 1
        return errors


    @getfromcache
    def getquickstats(self):
        if self.is_template_project:
            return empty_quickstats
        errors = self.require_units()
        stats = calculate_stats(Unit.objects.filter(store__translation_project=self))
        stats['errors'] = errors
        return stats

    @getfromcache
    def getcompletestats(self):
        if self.is_template_project:
            return empty_completestats
        for store in self.stores.filter(state__lt=CHECKED).iterator():
            store.require_qualitychecks()
        return group_by_count(QualityCheck.objects.filter(unit__store__translation_project=self), 'name')


    def _get_indexer(self):
        if self.non_db_state.indexer is None and self.non_db_state._indexing_enabled:
            try:
                indexer = self.make_indexer()
                if not self.non_db_state._index_initialized:
                    self.init_index(indexer)
                    self.non_db_state._index_initialized = True
                self.non_db_state.indexer =  indexer
            except Exception, e:
                logging.warning("Could not initialize indexer for %s in %s: %s", self.project.code, self.language.code, str(e))
                self.non_db_state._indexing_enabled = False

        return self.non_db_state.indexer

    indexer = property(_get_indexer)

    def _has_index(self):
        return self.non_db_state._indexing_enabled and \
            (self.non_db_state._index_initialized or self.indexer != None)

    has_index = property(_has_index)

    def update_file_from_version_control(self, store):
        try:
            hooks.hook(self.project.code, "preupdate", store.file.path)
        except:
            pass

        # keep a copy of working files in memory before updating
        oldstats = store.getquickstats()
        working_copy = store.file.store

        try:
            logging.debug("updating %s from version control", store.file.path)
            versioncontrol.updatefile(store.file.path)
            store.file._delete_store_cache()
            store.update(update_structure=True, update_translation=True, conservative=False)
            remotestats = store.getquickstats()
        except Exception, e:
            #something wrong, file potentially modified, bail out
            #and replace with working copy
            logging.error("near fatal catastrophe, exception %s while updating %s from version control", e, store.file.path)
            working_copy.save()
            raise VersionControlError

        #FIXME: try to avoid merging if file was not updated
        logging.debug("merging %s with version control update", store.file.path)
        store.mergefile(working_copy, "versionmerge", allownewstrings=False, suggestions=True, notranslate=False, obsoletemissing=False)

        try:
            hooks.hook(self.project.code, "postupdate", store.file.path)
        except:
            pass

        newstats = store.getquickstats()
        return oldstats, remotestats, newstats

    def update_project(self, request):
        """updates project translation files from version control,
        retaining uncommitted translations"""

        if not check_permission("commit", request):
            raise PermissionDenied(_("You do not have rights to update from version control here"))

        old_stats = self.getquickstats()
        remote_stats = {}

        for store in self.stores.iterator():
            try:
                oldstats, remotestats, newstats = self.update_file_from_version_control(store)
                remote_stats = dictsum(remote_stats, remotestats)
            except VersionControlError:
                pass

        project_tree.scan_translation_project_files(self)
        new_stats = self.getquickstats()

        request.user.message_set.create(message=unicode(_("Updated %s files from version control", self.fullname)))
        request.user.message_set.create(message=stats_message("working copy", old_stats))
        request.user.message_set.create(message=stats_message("remote copy", remote_stats))
        request.user.message_set.create(message=stats_message("merged copy", new_stats))

        post_vc_update.send(sender=self, oldstats=old_stats, remotestats=remote_stats, newstats=new_stats)

    def update_file(self, request, store):
        """updates file from version control, retaining uncommitted
        translations"""
        if not check_permission("commit", request):
            raise PermissionDenied(_("You do not have rights to update from version control here"))

        try:
            oldstats, remotestats, newstats = self.update_file_from_version_control(store)
            request.user.message_set.create(message=unicode(_("Updated file %s from version control", store.file.name)))
            request.user.message_set.create(message=stats_message("working copy", oldstats))
            request.user.message_set.create(message=stats_message("remote copy", remotestats))
            request.user.message_set.create(message=stats_message("merged copy", newstats))
            post_vc_update.send(sender=self, oldstats=oldstats, remotestats=remotestats, newstats=newstats)
        except VersionControlError:
            request.user.message_set.create(message=unicode(_("Failed to update %s from version control", store.file.name)))

        project_tree.scan_translation_project_files(self)

    def commitpofile(self, request, store):
        """commits an individual PO file to version control"""
        if not check_permission("commit", request):
            raise PermissionDenied(_("You do not have rights to commit files here"))

        store.sync(update_structure=True, update_translation=True, conservative=False)
        stats = store.getquickstats()
        author = request.user.username
        message = stats_message("Commit from %s by user %s." % (settings.TITLE, author), stats)
	# Try to append email as well, since some VCS does not allow omitting it (ie. Git).
	if request.user.is_authenticated() and len(request.user.email):
		author += " <%s>" % request.user.email

        try:
            filestocommit = hooks.hook(self.project.code, "precommit", store.file.path, author=author, message=message)
        except ImportError:
            # Failed to import the hook - we're going to assume there just isn't a hook to
            # import.    That means we'll commit the original file.
            filestocommit = [store.file.path]

        success = True
        try:
            for file in filestocommit:
                versioncontrol.commitfile(file, message=message, author=author)
                request.user.message_set.create(message="Committed file: <em>%s</em>" % file)
        except Exception, e:
            logging.error("Failed to commit files: %s", e)
            request.user.message_set.create(message="Failed to commit file: %s" % e)
            success = False
        try:
            hooks.hook(self.project.code, "postcommit", store.file.path, success=success)
        except:
            #FIXME: We should not hide the exception - makes development impossible
            pass
        post_vc_commit.send(sender=self, store=store, stats=stats, user=request.user, success=success)
        return success


    def initialize(self):
        try:
            hooks.hook(self.project.code, "initialize", self.real_path, self.language.code)
        except Exception, e:
            logging.error("Failed to initialize (%s): %s", self.language.code, e)

    ##############################################################################################

    def get_archive(self, stores):
        """returns an archive of the given filenames"""
        tempzipfile = None
        try:
            # using zip command line is fast
            from tempfile import mkstemp
            # The temporary file below is opened and immediately closed for security reasons
            fd, tempzipfile = mkstemp(prefix='pootle', suffix='.zip')
            os.close(fd)
            os.system("cd %s ; zip -r - %s > %s" % (self.abs_real_path, " ".join(store.abs_real_path[len(self.abs_real_path)+1:] for store in stores), tempzipfile))
            filedata = open(tempzipfile, "r").read()
            if filedata:
                return filedata
        finally:
            if tempzipfile is not None and os.path.exists(tempzipfile):
                os.remove(tempzipfile)

        # but if it doesn't work, we can do it from python
        archivecontents = cStringIO.StringIO()
        archive = zipfile.ZipFile(archivecontents, 'w', zipfile.ZIP_DEFLATED)
        for store in stores:
            archive.write(store.abs_real_path.encode('utf-8'), store.abs_real_path[len(self.abs_real_path)+1:].encode('utf-8'))
        archive.close()
        return archivecontents.getvalue()


    ##############################################################################################

    def make_indexer(self):
        """get an indexing object for this project

        Since we do not want to keep the indexing databases open for the lifetime of
        the TranslationProject (it is cached!), it may NOT be part of the Project object,
        but should be used via a short living local variable.
        """
        logging.debug("Loading indexer for %s", self.pootle_path)
        indexdir = os.path.join(self.abs_real_path, self.index_directory)
        index = indexing.get_indexer(indexdir)
        index.set_field_analyzers({
                        "pofilename": index.ANALYZER_EXACT,
                        "itemno": index.ANALYZER_EXACT,
                        "pomtime": index.ANALYZER_EXACT,
                        "dbid": index.ANALYZER_EXACT,
                        })
        return index

    def init_index(self, indexer):
        """initializes the search index"""
        #FIXME: stop relying on pomtime so virtual files can be searchable?
        for store in self.stores.iterator():
            self.update_index(indexer, store, optimize=False)


    def update_index(self, indexer, store, unitid=None, optimize=True):
        """updates the index with the contents of pofilename (limit to items if given)

        There are three reasons for calling this function:
            1. creating a new instance of L{TranslationProject} (see L{initindex})
                    -> check if the index is up-to-date / rebuild the index if necessary
            2. translating a unit via the web interface
                    -> (re)index only the specified unit(s)

        The argument L{item} should be None for 1.

        known problems:
            1. This function should get called, when the po file changes externally.
                 WARNING: You have to stop the pootle server before manually changing
                 po files, if you want to keep the index database in sync.

        @param unitid: pk of unit within the po file OR None (=rebuild all)
        @type unitid: int
        @param optimize: should the indexing database be optimized afterwards
        @type optimize: bool
        """
        #FIXME: leverage file updated signal to check if index needs updating
        if indexer == None:
            return False
        # check if the pomtime in the index == the latest pomtime
        pomtime = str(hash(store.get_mtime())**2)
        pofilenamequery = indexer.make_query([("pofilename", store.pootle_path)], True)
        pomtimequery = indexer.make_query([("pomtime", pomtime)], True)
        gooditemsquery = indexer.make_query([pofilenamequery, pomtimequery], True)
        gooditemsnum = indexer.get_query_result(gooditemsquery).get_matches_count()
        # if there is at least one up-to-date indexing item, then the po file
        # was not changed externally -> no need to update the database
        units = None
        if (gooditemsnum > 0) and (not unitid):
            # nothing to be done
            return
        elif unitid is not None:
            # Update only specific item - usually translation via the web
            # interface. All other items should still be up-to-date (even with an
            # older pomtime).
            # delete the relevant item from the database
            units = store.units.filter(id=unitid)
            itemsquery = indexer.make_query([("dbid", str(unitid))], False)
            indexer.delete_doc([pofilenamequery, itemsquery])
        else:
            # (item is None)
            # The po file is not indexed - or it was changed externally 
            # delete all items of this file
            logging.debug("Updating %s indexer for file %s", self.pootle_path, store.pootle_path) 
            indexer.delete_doc({"pofilename": store.pootle_path})
            units = store.units
        addlist = []
        for unit in units:
            doc = {"pofilename": store.pootle_path,
                   "pomtime": pomtime,
                   "itemno": str(unit.index),
                   "dbid": str(unit.id),
                   }
            if unit.hasplural():
                orig = "\n".join(unit.source.strings)
                trans = "\n".join(unit.target.strings)
            else:
                orig = unit.source
                trans = unit.target
            doc["source"] = orig
            doc["target"] = trans
            doc["notes"] = unit.getnotes()
            doc["locations"] = unit.getlocations()
            addlist.append(doc)
        if addlist:
            try:
                indexer.begin_transaction()
                for add_item in addlist:
                    indexer.index_document(add_item)
                indexer.commit_transaction()
                indexer.flush(optimize=optimize)
            except Exception, e:
                logging.error("Error opening indexer for %s:\n%s", self, e)
                try:
                    indexer.cancel_transaction()
                except:
                    pass

    ########################################################################################

    is_terminology_project = property(lambda self: self.pootle_path.endswith('/terminology/'))
    is_template_project = property(lambda self: self.pootle_path.startswith('/templates/'))

    def gettermmatcher(self):
        """returns the terminology matcher"""
        if self.is_terminology_project:
            if self.non_db_state.termmatcher is None:
                self.require_units()

            newmtime = self.get_mtime()
            if newmtime != self.non_db_state.termmatchermtime:
                self.non_db_state.termmatcher = match.terminologymatcher(self.stores.iterator())
                self.non_db_state.termmatchermtime = newmtime
        else:
            try:
                termfilename = "pootle-terminology." + self.project.localfiletype
                store = self.stores.get(name=termfilename)
                newmtime = store.get_mtime()
                if newmtime != self.non_db_state.termmatchermtime:
                    self.non_db_state.termmatcher = match.terminologymatcher(store)
                    self.non_db_state.termmatchermtime = newmtime
            except Store.DoesNotExist:
                try:
                    termproject = TranslationProject.objects.get(language=self.language_id, project__code='terminology')
                    self.non_db_state.termmatcher = termproject.gettermmatcher()
                    self.non_db_state.termmatchermtime = termproject.non_db_state.termmatchermtime
                except TranslationProject.DoesNotExist:
                    pass
        return self.non_db_state.termmatcher

    ##############################################################################################

    #FIXME: we should cache results to ease live translation
    def translate_message(self, singular, plural=None, n=1):
        for store in self.stores.iterator():
            unit = store.findunit(singular)
            if unit is not None and unit.istranslated():
                if unit.hasplural() and n != 1:
                    nplural = self.language.nplurals
                    pluralequation = self.language.pluralequation
                    if pluralequation:
                        pluralfn = gettext.c2py(pluralequation)
                        target =  unit.target.strings[pluralfn(n)]
                        if target is not None:
                            return target
                else:
                    return unit.target

        # no translation found
        if n != 1 and plural is not None:
            return plural
        else:
            return singular


def stats_message(version, stats):
    return "%s: %d of %d messages translated (%d fuzzy)." % \
           (version, stats.get("translated", 0), stats.get("total", 0), stats.get("fuzzy", 0))

def scan_languages(sender, instance, **kwargs):
    for language in Language.objects.iterator():
        create_translation_project(language, instance)
post_save.connect(scan_languages, sender=Project)

def scan_projects(sender, instance, **kwargs):
    for project in Project.objects.iterator():
        create_translation_project(instance, project)
post_save.connect(scan_projects, sender=Language)
