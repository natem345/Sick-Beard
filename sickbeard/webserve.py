# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of Sick Beard.
#
# Sick Beard is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Sick Beard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Sick Beard.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import with_statement

import os.path

import time
import urllib
import re
import threading
import datetime

from Cheetah.Template import Template
import cherrypy.lib

import sickbeard

from sickbeard import config
from sickbeard import history, notifiers, processTV
from sickbeard import tv, ui
from sickbeard import logger, helpers, exceptions, classes, db
from sickbeard import encodingKludge as ek
from sickbeard import search_queue
from sickbeard import image_cache

from sickbeard.providers import newznab
from sickbeard.common import Quality, Overview, statusStrings
from sickbeard.common import SNATCHED, DOWNLOADED, SKIPPED, UNAIRED, IGNORED, ARCHIVED, WANTED
from sickbeard.exceptions import ex


from lib.tvdb_api import tvdb_api

try:
    import json
except ImportError:
    from lib import simplejson as json

import xml.etree.cElementTree as etree

from sickbeard import browser


class PageTemplate (Template):
    def __init__(self, *args, **KWs):
        KWs['file'] = os.path.join(sickbeard.PROG_DIR, "data/interfaces/default/", KWs['file'])
        super(PageTemplate, self).__init__(*args, **KWs)
        self.sbRoot = sickbeard.WEB_ROOT
        self.projectHomePage = "http://code.google.com/p/sickbeard/"

        logPageTitle = 'Logs &amp; Errors'
        if len(classes.ErrorViewer.errors):
            logPageTitle += ' ('+str(len(classes.ErrorViewer.errors))+')'
        self.logPageTitle = logPageTitle
        self.sbPID = str(sickbeard.PID)
        self.menu = [
            { 'title': 'Home',            'key': 'home'           },
            { 'title': 'Coming Episodes', 'key': 'comingEpisodes' },
            { 'title': 'History',         'key': 'history'        },
            { 'title': 'Manage',          'key': 'manage'         },
            { 'title': 'Config',          'key': 'config'         },
            { 'title': logPageTitle,      'key': 'errorlogs'      },
        ]

def redirect(abspath, *args, **KWs):
    assert abspath[0] == '/'
    raise cherrypy.HTTPRedirect(sickbeard.WEB_ROOT + abspath, *args, **KWs)

class TVDBWebUI:
    def __init__(self, config, log=None):
        self.config = config
        self.log = log

    def selectSeries(self, allSeries):

        searchList = ",".join([x['id'] for x in allSeries])
        showDirList = ""
        for curShowDir in self.config['_showDir']:
            showDirList += "showDir="+curShowDir+"&"
        redirect("/home/addShows/addShow?" + showDirList + "seriesList=" + searchList)

def _munge(string):
    return unicode(string).encode('utf-8', 'xmlcharrefreplace')

def _genericMessage(subject, message):
    t = PageTemplate(file="genericMessage.tmpl")
    t.submenu = HomeMenu()
    t.subject = subject
    t.message = message
    return _munge(t)

def _getEpisode(show, season, episode):

    if show == None or season == None or episode == None:
        return "Invalid parameters"

    showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

    if showObj == None:
        return "Show not in show list"

    epObj = showObj.getEpisode(int(season), int(episode))

    if epObj == None:
        return "Episode couldn't be retrieved"

    return epObj


#removed classes




from sickbeard.webserve_manage import *
from sickbeard.webserve_home import *
from sickbeard.webserve_history import *
from sickbeard.webserve_config import *

class UI:
    
    @cherrypy.expose
    def add_message(self):
        
        ui.notifications.message('Test 1', 'This is test number 1')
        ui.notifications.error('Test 2', 'This is test number 2')
        
        return "ok"

    @cherrypy.expose
    def get_messages(self):
        messages = {}
        cur_notification_num = 1
        for cur_notification in ui.notifications.get_notifications():
            messages['notification-'+str(cur_notification_num)] = {'title': cur_notification.title,
                                                                   'message': cur_notification.message,
                                                                   'type': cur_notification.type}
            cur_notification_num += 1

        return json.dumps(messages)

class WebInterface:

    @cherrypy.expose
    def index(self):

        redirect("/home")

    @cherrypy.expose
    def export_shows(self):
        redirect("/export_shows")
    @cherrypy.expose
    
    def showPoster(self, show=None, which=None):

        if which == 'poster':
            default_image_name = 'poster.png'
        else:
            default_image_name = 'banner.png'

        default_image_path = ek.ek(os.path.join, sickbeard.PROG_DIR, 'data', 'images', default_image_name)
        if show == None:
            return cherrypy.lib.static.serve_file(default_image_path, content_type="image/jpeg")
        else:
            showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            return cherrypy.lib.static.serve_file(default_image_path, content_type="image/jpeg")

        cache_obj = image_cache.ImageCache()
        
        if which == 'poster':
            image_file_name = cache_obj.poster_path(showObj.tvdbid)
        # this is for 'banner' but also the default case
        else:
            image_file_name = cache_obj.banner_path(showObj.tvdbid)

        if ek.ek(os.path.isfile, image_file_name):
            try:
                from PIL import Image
                from cStringIO import StringIO
            except ImportError: # PIL isn't installed
                return cherrypy.lib.static.serve_file(image_file_name, content_type="image/jpeg")
            else:
                im = Image.open(image_file_name)
                if im.mode == 'P': # Convert GIFs to RGB
                    im = im.convert('RGB')
                if which == 'banner':
                    size = 600, 112
                elif which == 'poster':
                    size = 136, 200
                else:
                    return cherrypy.lib.static.serve_file(image_file_name, content_type="image/jpeg")
                im = im.resize(size, Image.ANTIALIAS)
                buffer = StringIO()
                im.save(buffer, 'JPEG', quality=85)
                cherrypy.response.headers['Content-Type'] = 'image/jpeg'
                return buffer.getvalue()
        else:
            return cherrypy.lib.static.serve_file(default_image_path, content_type="image/jpeg")

    @cherrypy.expose
    def setComingEpsLayout(self, layout):
        if layout not in ('poster', 'banner', 'list'):
            layout = 'banner'
        
        sickbeard.COMING_EPS_LAYOUT = layout
        
        redirect("/comingEpisodes")

    @cherrypy.expose
    def toggleComingEpsDisplayPaused(self):
        
        sickbeard.COMING_EPS_DISPLAY_PAUSED = not sickbeard.COMING_EPS_DISPLAY_PAUSED
        
        redirect("/comingEpisodes")

    @cherrypy.expose
    def setComingEpsSort(self, sort):
        if sort not in ('date', 'network', 'show'):
            sort = 'date'
        
        sickbeard.COMING_EPS_SORT = sort
        
        redirect("/comingEpisodes")

    @cherrypy.expose
    def comingEpisodes(self, layout="None"):

        myDB = db.DBConnection()
        
        today = datetime.date.today().toordinal()
        next_week = (datetime.date.today() + datetime.timedelta(days=7)).toordinal()
        recently = (datetime.date.today() - datetime.timedelta(days=3)).toordinal()

        done_show_list = []
        qualList = Quality.DOWNLOADED + Quality.SNATCHED + [ARCHIVED, IGNORED]
        sql_results = myDB.select("SELECT *, tv_shows.status as show_status FROM tv_episodes, tv_shows WHERE season != 0 AND airdate >= ? AND airdate < ? AND tv_shows.tvdb_id = tv_episodes.showid AND tv_episodes.status NOT IN ("+','.join(['?']*len(qualList))+")", [today, next_week] + qualList)
        for cur_result in sql_results:
            done_show_list.append(int(cur_result["showid"]))

        more_sql_results = myDB.select("SELECT *, tv_shows.status as show_status FROM tv_episodes outer_eps, tv_shows WHERE season != 0 AND showid NOT IN ("+','.join(['?']*len(done_show_list))+") AND tv_shows.tvdb_id = outer_eps.showid AND airdate = (SELECT airdate FROM tv_episodes inner_eps WHERE inner_eps.showid = outer_eps.showid AND inner_eps.airdate >= ? ORDER BY inner_eps.airdate ASC LIMIT 1) AND outer_eps.status NOT IN ("+','.join(['?']*len(Quality.DOWNLOADED+Quality.SNATCHED))+")", done_show_list + [next_week] + Quality.DOWNLOADED + Quality.SNATCHED)
        sql_results += more_sql_results

        more_sql_results = myDB.select("SELECT *, tv_shows.status as show_status FROM tv_episodes, tv_shows WHERE season != 0 AND tv_shows.tvdb_id = tv_episodes.showid AND airdate < ? AND airdate >= ? AND tv_episodes.status = ? AND tv_episodes.status NOT IN ("+','.join(['?']*len(qualList))+")", [today, recently, WANTED] + qualList)
        sql_results += more_sql_results

        #epList = sickbeard.comingList

        # sort by air date
        sorts = {
            'date': (lambda x, y: cmp(int(x["airdate"]), int(y["airdate"]))),
            'show': (lambda a, b: cmp(a["show_name"], b["show_name"])),
            'network': (lambda a, b: cmp(a["network"], b["network"])),
        }

        #epList.sort(sorts[sort])
        sql_results.sort(sorts[sickbeard.COMING_EPS_SORT])

        t = PageTemplate(file="comingEpisodes.tmpl")
        paused_item = { 'title': '', 'path': 'toggleComingEpsDisplayPaused' }
        paused_item['title'] = 'Hide Paused' if sickbeard.COMING_EPS_DISPLAY_PAUSED else 'Show Paused'
        t.submenu = [
            { 'title': 'Sort by:', 'path': {'Date': 'setComingEpsSort/?sort=date',
                                            'Show': 'setComingEpsSort/?sort=show',
                                            'Network': 'setComingEpsSort/?sort=network',
                                           }},

            { 'title': 'Layout:', 'path': {'Banner': 'setComingEpsLayout/?layout=banner',
                                           'Poster': 'setComingEpsLayout/?layout=poster',
                                           'List': 'setComingEpsLayout/?layout=list',
                                           }},
            paused_item,
        ]

        t.next_week = next_week
        t.today = today
        t.sql_results = sql_results

        # Allow local overriding of layout parameter
        if layout and layout in ('poster', 'banner', 'list'):
            t.layout = layout
        else:
            t.layout = sickbeard.COMING_EPS_LAYOUT
                

        return _munge(t)







    from sickbeard.webserve_manage import *
    from sickbeard.webserve_home import *
    from sickbeard.webserve_history import *
    from sickbeard.webserve_config import *
	
    manage = Manage()


    history = History()


    config = Config()

    home = Home()


    browser = browser.WebFileBrowser()

    errorlogs = ErrorLogs()
    
    ui = UI()
