#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright 2004-2009 Zuza Software Foundation
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

import logging

from django.core.cache import cache
from django.conf import settings
from django.core.paginator import Paginator
from django.utils.encoding import iri_to_uri

def getfromcache(function, timeout=settings.OBJECT_CACHE_TIMEOUT):
    def _getfromcache(instance, *args, **kwargs):
        key = iri_to_uri(instance.pootle_path + ":" + function.__name__)
        result = cache.get(key)
        if result is None:
            logging.debug("cache miss for %s", key)
            result = function(instance, *args, **kwargs)
            cache.set(key, result, timeout)
        return result
    return _getfromcache

def deletefromcache(sender, functions, **kwargs):
    path = iri_to_uri(sender.pootle_path)
    path_parts = path.split("/")

    # clean project cache
    if len(path_parts):
        key = "/projects/%s/" % path_parts[2]
        for func in functions:
            cache.delete(key + ":"+func)

    # clean store and directory cache
    while path_parts:
        for func in functions:
            cache.delete(path + ":"+func)
        path_parts = path_parts[:-1]
        path = "/".join(path_parts) + "/"

def dictsum(x, y):
    return dict( (n, x.get(n, 0)+y.get(n, 0)) for n in set(x)|set(y) )


def paginate(request, queryset, items=30, page=None):
    paginator = Paginator(queryset, items, orphans=items/2)

    if not page:
        try:
            page = int(request.GET.get('page', 1))
        except ValueError:
            # wasn't an int use 1
            page = 1
    # page value too large
    page = min(page, paginator.num_pages)

    return paginator.page(page)
