#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 
# Copyright 2008 Zuza Software Foundation
# 
# This file is part of translate.
#
# translate is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# 
# translate is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with translate; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import bisect

from django.db import models
from django.conf import settings
from django.db.models.signals import pre_delete, pre_save

from translate.storage import statsdb

from pootle_app.goals import StoreAssignment

from Pootle.pootlefile import relative_real_path, absolute_real_path, with_pootle_file

def add_to_stats(stats, other_stats):
    for key, value in other_stats.iteritems():
        stats[key] = stats.get(key, 0) + value

################################################################################

def member(sorted_set, element):
    """Check whether element appears in sorted_set."""
    pos = bisect.bisect_left(sorted_set, element)
    if pos < len(sorted_set):
        return sorted_set[pos] == element
    else:
        return False

def as_seq_with_len(seq):
    if not hasattr(seq.__class__, '__len__'):
        return list(seq)
    else:
        return seq

def intersect(set_a, set_b):
    """Find the intersection of the sorted sets set_a and set_b."""
    # If both set_a and set_b have elements
    set_a = as_seq_with_len(set_a)
    set_b = as_seq_with_len(set_b)
    if len(set_b) != 0 and len(set_a) != 0:
        # Find the position of the element in set_a that is at least
        # as large as the minimum element in set_b.
        start_a = bisect.bisect_left(set_a, set_b[0])
        # For each element in set_a...
        for element in set_a[start_a:]:
            # ...which is also in set_b...
            if member(set_b, element):
                yield element

def narrow_to_last_item_range(translatables, last_index):
    return translatables[last_index + 1:]

def narrow_to_index_subset(translatables, index_subset):
    if index_subset is not None:
        return intersect(index_subset, translatables)
    else:
        return translatables

def narrow_to_search_text(store, translatables, search):
    def do_slow_search(pootle_file):
        # We'll get here if the user is searching for a piece of text and if no indexer
        # (such as Xapian or Lucene) is usable. First build a grepper...
        grepfilter = pogrep.GrepFilter(search.searchtext, search.searchfields, ignorecase=True)
        # ...then filter the items using the grepper.
        return (index for index in translatables 
                        if grepfilter.filterunit(pootle_file.items[item]))

    if search.search_text is not None:
        return with_pootle_file(search.translation_project, store.abs_real_path, do_slow_search)
    else:
        return translatables

def narrow_to_assigns(store, translatables, search):
    if len(search.assigned_to) > 0:
        assignments = StoreAssignment.objects.filter(assignment__in=search.assigned_to, store=store)
        assigned_indices = reduce(set.__or__, [assignment.unit_assignments for assignment in assignments], set())
        return intersect(sorted(assigned_indices), translatables)    
    else:
        return translatables

def narrow_to_matches(stats, translatables, search):
    if len(search.match_names) > 0:
        matches = reduce(set.__or__,
                         (set(stats[match_name]) for match_name in search.match_names if match_name in stats),
                         set())
        return intersect(sorted(matches), translatables)
    else:
        return translatables

class Search(object):
    @classmethod
    def from_request(cls, request):
        def get_list(request, name):
            try:
                return request.GET[name].split(',')
            except KeyError:
                return []

        kwargs = {}
        if 'goal' in request.GET:
            kwargs['goal'] = Goal.objects.get(name=request.GET['goal'])
        kwargs['match_names']         = get_list(request, 'match_names')
        kwargs['assigned_to']         = get_list(request, 'assigned_to')
        kwargs['search_text']         = request.GET.get('search_text', None)
        kwargs['search_fields']       = get_list(request, 'search_fields')
        kwargs['translation_project'] = request.translation_project
        return cls(**kwargs)

    def __init__(self, goal=None, match_names=[],
                 assigned_to=[], search_text=None, search_fields=None,
                 translation_project=None):
        self.goal            = goal
        self.match_names     = match_names
        self.assigned_to     = assigned_to
        self.search_text     = search_text
        self.search_fields   = search_fields
        if search_fields is None: # TDB: This is wrong
            search_fiels = ['source', 'target']
        self.translation_project = translation_project

    def contains_only_file_specific_criteria(self):
        return self.search_text is None  and \
            self.match_names == [] and \
            self.assigned_to == []

    def _get_index_results():
        if self._index_results is None:
            self._index_results = compute_index_result(self)
        return self._index_results

    def _all_matches(self, store, stats, range, index_subset):
        return \
            narrow_to_search_text( # Reduce to the search results. This is called when 
                store,             # we don't use a search indexer
                narrow_to_assigns( # Reduce to everything matching the current assingment
                    store,
                    narrow_to_matches( # Reduce to everything matching names like xmltags
                        stats,
                        narrow_to_index_subset( 
                            stats['total'][range[0]:range[1]],
                            index_subset),
                        self),
                    self),
                self)

    def next_matches(self, store, last_index, index_subset=None):
        # stats['total'] is an array of indices into the units array
        # of a store. But we want indices of the units that we see in
        # Pootle. bisect.bisect_left of a member in stats['total']
        # gives us the index of the unit as we see it in Pootle.
        if last_index < 0:
            last_index = 0
        if self.contains_only_file_specific_criteria():
            # This is a special shortcut that we use when we don't
            # need to narrow our search based on unit-specific
            # properties. In this case, we know that last_item is the
            # sought after item, unless of course item >= number of
            # units
            stats = store.get_stats_totals(self.translation_project.checker)
            if last_index < stats['total']:
                return iter([last_index])
            else:
                return iter([])
        else:
            stats = store.get_property_stats(self.translation_project.checker)
            matches = list(self._all_matches(store, stats, (last_index, None), index_subset))
            return (bisect.bisect_left(stats['total'], item) for item in matches)

    def prev_matches(self, store, last_index, index_subset=None):
        if last_index < 0:
            # last_index = -1 means that last_index is
            # unspecified. Normally this means that we want to start
            # searching at the start of stores. But in reverse
            # iteration mode, we view len(stats['total']) as
            # equivalent to the position of -1. This is because we
            # consider all elements in stats['total'] in the range [0,
            # last_index]. Thus, if we don't yet have a valid index
            # into the file, we want to include the very last element
            # of stats['total'] as well when searching. Thus
            # [0:len(stats['total'])] gives us what we need.
            last_index = len(stats['total']) - 1
        if self.contains_only_file_specific_criteria():
            # This is a special shortcut that we use when we don't
            # need to narrow our search based on unit-specific
            # properties. In this case, we know that last_item is the
            # sought after item.
            # units
            return iter([last_index])
        else:
            stats = store.get_property_stats(self.translation_project.checker)
            matches = list(self._all_matches(store, stats, (0, last_index + 1), index_subset))
            return (bisect.bisect_left(stats['total'], item) for item in reversed(matches))

def search_from_state(translation_project, search_state):
    return Search(translation_project=translation_project, **search_state.as_dict())

################################################################################

class DirectoryManager(models.Manager):
    def get_query_set(self, *args, **kwargs):
        return super(DirectoryManager, self).get_query_set(*args, **kwargs).select_related(depth=1)

    def _get_root(self):
        return self.get(parent=None)

    root = property(_get_root)

def filter_goals(query, goal):
    if goal is None:
        return query
    else:
        return query.filter(goals=goal)

def filter_next_store(query, store_name):
    if store_name is None:
        return query
    else:
        return query.filter(name__gte=store_name)

class Directory(models.Model):
    class Meta:
        ordering = ['name']

    is_dir = True

    name        = models.CharField(max_length=255, null=False, db_index=True)
    parent      = models.ForeignKey('Directory', related_name='child_dirs', null=True)
    pootle_path = models.CharField(max_length=1024, null=False)

    objects = DirectoryManager()

    def get_relative(self, path):
        """Given a path of the for a/b/c, where the path is relative
        to this directory, recurse the path and return the object
        (either a Directory or a Store) named 'c'.

        This does not currently deal with .. path components."""

        if path not in (None, ''):
            pootle_path = '%s%s' % (self.pootle_path, path)
            try:
                return Directory.objects.get(pootle_path=pootle_path)
            except Directory.DoesNotExist, e:
                try:
                    return Store.objects.get(pootle_path=pootle_path)
                except Store.DoesNotExist:
                    raise e
        else:
            return self

    def filter_stores(self, search=Search(), starting_store=None):
        if search.contains_only_file_specific_criteria():
            return filter_next_store(filter_goals(self.child_stores, search.goal), starting_store)
        else:
            raise Exception("Can't filter on unit-specific information")

    def get_or_make_subdir(self, child_name):
        try:
            return self.child_dirs.get(name=child_name)
        except Directory.DoesNotExist:
            child_dir = Directory(name=child_name, parent=self)
            child_dir.save()
            return child_dir

    def num_stores(self, search=Search()):
        import store_iteration

        return sum(1 for x in store_iteration.iter_stores(self, search=search))

    def get_stats_totals(self, checker, search=Search()):
        import store_iteration

        result = statsdb.emptyfiletotals()
        for store in store_iteration.iter_stores(self, search=search):
            add_to_stats(result, store.get_stats_totals(checker, search))
        return result

    def get_quick_stats(self, checker, search=Search()):
        import store_iteration

        result = statsdb.emptyfiletotals()
        for store in store_iteration.iter_stores(self, search=search):
            add_to_stats(result, store.get_quick_stats(checker))
        return result

    def parent_chain(self):
        def enum_chain(start_dir):
            directory = start_dir
            while directory.parent is not None:
                yield directory
                directory = directory.parent
        return reversed(list(enum_chain(self)))

    def __str__(self):
        return self.name

def delete_children(sender, instance, **kwargs):
    """Before deleting a directory, delete all its children."""
    for child_store in instance.child_stores.all():
        child_store.delete()

    for child_dir in instance.child_dirs.all():
        child_dir.delete()

pre_delete.connect(delete_children, sender=Directory)

def set_directory_pootle_path(sender, instance, **kwargs):
    if instance.parent is not None:
        instance.pootle_path = '%s%s/' % (instance.parent.pootle_path, instance.name)
    else:
        instance.pootle_path = '/'

pre_save.connect(set_directory_pootle_path, sender=Directory)

################################################################################

def walk(directory, f):
    def walk_loop(directory_path, directory, f):
        child_dirs = directory.child_dirs.all()
        f(directory_path, directory, child_dirs, top_dir.child_stores.all())
        for child_dir in child_dirs:
            walk_loop(directory_path + os.sep + child_dir.name, directory, f)

    walk_loop("/".join(directory.parent_chain()), directory, f)

def collect_goals(directory):
    goal_set = set()
    def add_goals(store):
        goal_set = goal_set + set(store.goals.all())
    walk_stores(directory, None, add_goals)
    return goal_set

################################################################################

def get_stats_cache():
    return statsdb.StatsCache(settings.STATS_DB_PATH)

class StoreManager(models.Manager):
    def get_query_set(self, *args, **kwargs):
        return super(StoreManager, self).get_query_set(*args, **kwargs).select_related(depth=1)

class Store(models.Model):
    """A model representing a translation store (i.e. a PO or XLIFF file)."""

    objects = StoreManager()
    
    is_dir = False

    class Meta:
        ordering = ['name']
        unique_together = ('parent', 'name')

    real_path   = models.FilePathField()
    # Uncomment the line below when the Directory model comes into use
    parent      = models.ForeignKey('Directory', related_name='child_stores')
    # The filesystem path of the store.
    name        = models.CharField(max_length=255, null=False)
    pootle_path = models.CharField(max_length=1024, null=False)

    def _get_abs_real_path(self):
        return absolute_real_path(self.real_path)

    def _set_abs_real_path(self, value):
        self.real_path = relative_real_path(value)

    abs_real_path = property(_get_abs_real_path, _set_abs_real_path)

    def get_property_stats(self, checker, *args):
        try:
            return get_stats_cache().filestats(self.abs_real_path, checker) or \
                statsdb.emptyfilestats()
        except:
            return statsdb.emptyfilestats()
        
    def get_stats_totals(self, checker, *args):
        def compute_lengths(full_stats):
            return dict((key, len(value)) for key, value in full_stats.iteritems())

        totals = self.get_quick_stats(checker, *args)
        totals.update(compute_lengths(self.get_property_stats(checker, *args)))
        return totals

    def get_quick_stats(self, checker, *args):
        try:
            return get_stats_cache().filetotals(self.abs_real_path) or statsdb.emptyfiletotals()
        except:
            return statsdb.emptyfiletotals()

    def parent_chain(self):
        chain = self.parent.parent_chain()
        chain.append(self.name)
        return chain

    def num_stores(self, search=None):
        return 1

    def __str__(self):
        return self.name

def set_store_pootle_path(sender, instance, **kwargs):
    instance.pootle_path = '%s%s' % (instance.parent.pootle_path, instance.name)

pre_save.connect(set_store_pootle_path, sender=Store)

################################################################################

