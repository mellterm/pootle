#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2008-2009 Zuza Software Foundation
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

import cStringIO
import os

from django.http import HttpResponse, HttpResponseRedirect, HttpResponsePermanentRedirect
from django.contrib.auth.decorators import user_passes_test
from django.utils import simplejson
from django.utils.translation import ugettext as _
from django.utils.translation import ungettext as _N

from Pootle import indexpage, pan_app, projects
from Pootle.misc.jtoolkit_django import process_django_request_args

from pootle_app.views.util         import render_to_kid, render_jtoolkit
from pootle_app.lib.util           import redirect
from pootle_app.models             import Language, Project, TranslationProject, Store, Directory, store_iteration
from pootle_app.models.search      import Search, search_from_state
from pootle_app.url_manip          import strip_trailing_slash, clear_path
from pootle_app.models.permissions import get_matching_permissions, PermissionError, check_permission
from pootle_app.models.profile     import get_profile
from pootle_app.views.language     import dispatch
from pootle_app.convert            import convert_table

from project_index import view as project_index_view
from translate_page import find_and_display
from admin import view as translation_project_admin_view

from Pootle import pootlefile
from pootle_app import unit_update

################################################################################

def get_language(f):
    def decorated_f(request, language_code, *args, **kwargs):
        try:
            language = Language.objects.get(code=language_code)
            return f(request, language, *args, **kwargs)
        except Language.DoesNotExist:
            return redirect('/', message=_("The language %s is not defined for this Pootle installation" % language_code))                    
    return decorated_f

def get_translation_project(f):
    def decorated_f(request, language_code, project_code, *args, **kwargs):
        try:
            translation_project = TranslationProject.objects.select_related(depth=1).get(language__code=language_code,
                                                                                         project__code=project_code)
            return f(request, translation_project, *args, **kwargs)
        except TranslationProject.DoesNotExist:
            # No such TranslationProject.  It might be because the
            # language code doesn't exist...
            if Language.objects.filter(code=language_code).count() == 0:
                return redirect('/', message=_('The language "%s" is not defined for this Pootle installation' % language_code))
            # ...or if the language exists, maybe the project code is
            # invalid...
            elif Project.objects.filter(code=project_code).count() == 0:
                return redirect('/', message=_('The project "%s" is not defined for this Pootle installation' % project_code))
            # ...but if both the language and project codes are valid,
            # then we simply don't have a corresponding
            # TranslationProject
            else:
                return redirect('/%s' % language_code, message=_('The project "%s" does not exist for the language %s' % (project_code, language_code)))
    return decorated_f

def set_request_context(f):
    def decorated_f(request, translation_project, *args, **kwargs):
        # For now, all permissions in a translation project are
        # relative to the root of that translation project.
        request.permissions = get_matching_permissions(
            get_profile(request.user), translation_project.directory)
        request.translation_project = translation_project
        return f(request, translation_project, *args, **kwargs)
    return decorated_f

################################################################################

@get_language
def language_index(request, language):
    return render_jtoolkit(indexpage.LanguageIndex(language, request))

@get_translation_project
@set_request_context
def translation_project_admin(request, translation_project):
    return translation_project_admin_view(request, translation_project)

@get_translation_project
@set_request_context
def translate_page(request, translation_project, dir_path):
    try:
        def next_store_item(search, store_name, item):
            return store_iteration.get_next_match(directory,
                                                  store_name,
                                                  item,
                                                  search)

        def prev_store_item(search, store_name, item):
            return store_iteration.get_prev_match(directory,
                                                  store_name,
                                                  item,
                                                  search)

        directory = translation_project.directory.get_relative(dir_path)
        return find_and_display(request, directory, next_store_item, prev_store_item)
    except PermissionError, msg:
        return redirect('/%s/%s/' % (translation_project.language.code, translation_project.project.code), message=msg)

@get_translation_project
@set_request_context
def project_index(request, translation_project, dir_path):
    directory = Directory.objects.get(pootle_path=translation_project.directory.pootle_path + dir_path)
    try:
        return project_index_view(request, translation_project, directory)
    except PermissionError, msg:
        return redirect('/%s/%s/' % (translation_project.language.code, translation_project.project.code), message=msg)

def handle_translation_file(request, translation_project, file_path):
    pootle_path = translation_project.directory.pootle_path + (file_path or '')
    store = Store.objects.get(pootle_path=pootle_path)
    try:
        def get_item(itr, item):
            try:
                return itr.next()
            except StopIteration:
                return item

        def next_store_item(search, store_name, item):
            return store, get_item(search.next_matches(store, item), item - 1)

        def prev_store_item(search, store_name, item):
            if item > 0:
                return store, get_item(search.prev_matches(store, item), item + 1)
            else:
                return store, 0

        return find_and_display(request, store.parent, next_store_item, prev_store_item)
    except PermissionError, e:
        return redirect('/%s/%s/' % (translation_project.language.code, translation_project.project.code), 
                        message=e.args[0])

@get_translation_project
@set_request_context
def export_zip(request, translation_project, file_path):
    if not check_permission("archive", request):
        return redirect('/%s/%s' % (translation_project.language.code, translation_project.project.code),
                        message=_('You do not have the right to create ZIP archives.'))
    pootle_path = translation_project.directory.pootle_path + (file_path or '')
    try:
        path_obj = Directory.objects.get(pootle_path=pootle_path)
    except Directory.DoesNotExist:
        path_obj = Store.objects.get(pootle_path=pootle_path[:-1])
    stores = store_iteration.iter_stores(path_obj, Search.from_request(request))
    archivecontents = translation_project.get_archive(stores)
    return HttpResponse(archivecontents, content_type="application/zip")

@get_translation_project
@set_request_context
def export_sdf(request, translation_project, file_path):
    if not check_permission("pocompile", request):
        return redirect('/%s/%s' % (translation_project.language.code, translation_project.project.code),
                        message=_('You do not have the right to create SDF files.'))
    return HttpResponse(translation_project.getoo(), content_type="text/tab-separated-values")

MIME_TYPES = {
    "po":  "text/x-gettext-translation; charset=%(encoding)s",
    "csv": "text/csv; charset=%(encoding)s",
    "xlf": "application/x-xliff; charset=%(encoding)s",
    "ts":  "application/x-linguist; charset=%(encoding)s",
    "mo":  "application/x-gettext-translation" }

@get_translation_project
@set_request_context
def export(request, translation_project, file_path, format):
    def send(pootle_file):
        encoding = getattr(pootle_file, "encoding", "UTF-8")
        content_type = MIME_TYPES[format] % dict(encoding=encoding)
        if format == translation_project.project.localfiletype:
            return HttpResponse(str(pootle_file), content_type=content_type)
        else:
            convert_func = convert_table[translation_project.project.localfiletype, format]
            output_file = cStringIO.StringIO()
            input_file  = cStringIO.StringIO(str(pootle_file))
            convert_func(input_file, output_file, None)
            return HttpResponse(output_file.getvalue(), content_type=content_type)
    store = Store.objects.get(pootle_path=translation_project.directory.pootle_path + file_path)
    return pootlefile.with_store(translation_project, store, send)

@get_translation_project
@set_request_context
def handle_file(request, translation_project, file_path):
    return handle_translation_file(request, translation_project, file_path)

@get_translation_project
@set_request_context
def handle_suggestions(request, translation_project, file_path, item):
    """Handles accepts/rejects of suggestions selectively via AJAX, receiving
       and sending data in JSON format.

       Response attributes are described below:
        * "status": Indicates the status after trying the action.
                    Possible values: "error", "success".
        * "message": Message status of the transaction. Depending on the status
                    it will display an error message, or the number of
                    suggestions rejected/accepted."""
    # TODO: finish this function and return nice diffs
    pootle_path = translation_project.directory.pootle_path + file_path
    store = Store.objects.get(pootle_path=pootle_path)
    file_path = pootlefile.absolute_real_path(store.real_path)
    
    def getpendingsuggestions(item):
        """Gets pending suggestions for item in pofilename."""
        itemsuggestions = []
        suggestions = translation_project.getsuggestions(file_path, item)
        for suggestion in suggestions:
            if suggestion.hasplural():
                itemsuggestions.append(suggestion.target.strings)
            else:
                itemsuggestions.append([suggestion.target])
        return itemsuggestions

    response = {}
    # Decode JSON data sent via POST
    data = simplejson.loads(request.POST.get("data", "{}"))
    if not data:
        response["status"] = "error"
        response["message"] = _("No suggestion data given.")
    else:
        # TODO: handle plurals
        pofilename = clear_path(file_path)

        rejects = data.get("rejects", [])
        reject_candidates = len(rejects)
        reject_count = 0
        accepts = data.get("accepts", [])
        accept_candidates = len(accepts)
        accept_count = 0

        for sugg in reversed(rejects):
            try:
                # XXX: disabled for testing
                pootlefile.with_store(translation_project, store,
                                      lambda pootle_file:
                                      unit_update.reject_suggestion(pootle_file,
                                                                    int(item), int(sugg["id"]),
                                                                    sugg["newtrans"], request
                                                                    )
                                      )
                
                reject_count += 1
                # TODO: convert this a list
                response["deleted"] = item + "-" + sugg["id"]
                pending = getpendingsuggestions(int(item))
            except ValueError:
                # TODO: provide fine-grained error message from the exception
                response["message"] = _("This is an error message.")

        for sugg in accepts:
            try:
                # XXX: disabled for testing
                pootlefile.with_store(translation_project, store,
                                      lambda pootle_file:
                                      unit_update.accept_suggestion(pootle_file,
                                                                    int(item), int(sugg["id"]),
                                                                    sugg["newtrans"], request
                                                                    )
                                      )
                accept_count += 1
            except ValueError:
                # TODO: provide fine-grained error message from the exception
                response["message"] = _("This is an error message.")

        response["status"] = (reject_candidates == reject_count and
                              accept_candidates == accept_count) and \
                              "success" or "error"

        if response["status"] == "success":
            amsg = ""
            rmsg = ""
            if accept_candidates != 0:
                amsg = _("Suggestion accepted")
            if reject_candidates != 0:
                rmsg = _N("Suggestion rejected",
                          "%d suggestions rejected.", reject_count)
            response["message"] = amsg + rmsg

    response = simplejson.dumps(response, indent=4)
    # TODO: change mimetype to something more appropriate once all works fine
    return HttpResponse(response, mimetype="text/plain")

