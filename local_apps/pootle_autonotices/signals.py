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

"""Set of singal handlers for generating automatic notifications on system events"""



from pootle_notifications.models import Notice
from pootle_app.models import Directory
from pootle_misc.baseurl import l

##### Model Events #####

def new_object(created, message, parent):
    if created:
        notice = Notice(directory=parent, message=message)
        notice.save()

    
def new_language(sender, instance, created=False, **kwargs):
    message = 'New language <a href="%s">%s</a> created.' % (instance.get_absolute_url(), instance.fullname)
    new_object(created, message, instance.directory.parent)

def new_project(sender, instance, created=False, **kwargs):
    message = 'New project <a href="%s">%s</a> created.' % (instance.get_absolute_url(), instance.fullname)
    new_object(created, message, parent=Directory.objects.root)

def new_user(sender, instance, created=False, **kwargs):
    message = 'New user <a href="%s">%s</a> registered.' % (l('/accounts/%s/' % instance.username), instance.username)
    new_object(created, message, parent=Directory.objects.root)

def new_translationproject(sender, instance, created=False, **kwargs):
    message = 'New project <a href="%s">%s</a> added to language <a href="%s">%s</a>.' % (
        instance.get_absolute_url(), instance.project.fullname,
        instance.language.get_absolute_url(), instance.language.fullname)
    new_object(created, message, instance.directory.parent)
