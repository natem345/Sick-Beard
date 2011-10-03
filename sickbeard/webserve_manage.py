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

from sickbeard.webserve import *
from sickbeard.webserve import _munge, redirect, _genericMessage, _getEpisode

from lib.tvdb_api import tvdb_api

try:
    import json
except ImportError:
    from lib import simplejson as json

import xml.etree.cElementTree as etree

from sickbeard import browser

ManageMenu = [
    { 'title': 'Backlog Overview',          'path': 'manage/backlogOverview' },
    { 'title': 'Manage Searches',           'path': 'manage/manageSearches'  },
    { 'title': 'Episode Status Management', 'path': 'manage/episodeStatuses' },
]

class ManageSearches:

    @cherrypy.expose
    def index(self):
        t = PageTemplate(file="manage_manageSearches.tmpl")
        #t.backlogPI = sickbeard.backlogSearchScheduler.action.getProgressIndicator()
        t.backlogPaused = sickbeard.searchQueueScheduler.action.is_backlog_paused() #@UndefinedVariable
        t.backlogRunning = sickbeard.searchQueueScheduler.action.is_backlog_in_progress() #@UndefinedVariable
        t.searchStatus = sickbeard.currentSearchScheduler.action.amActive #@UndefinedVariable
        t.submenu = ManageMenu

        return _munge(t)

    @cherrypy.expose
    def forceSearch(self):

        # force it to run the next time it looks
        result = sickbeard.currentSearchScheduler.forceRun()
        if result:
            logger.log(u"Search forced")
            ui.notifications.message('Episode search started',
                          'Note: RSS feeds may not be updated if retrieved recently')

        redirect("/manage/manageSearches")

    @cherrypy.expose
    def pauseBacklog(self, paused=None):
        if paused == "1":
            sickbeard.searchQueueScheduler.action.pause_backlog() #@UndefinedVariable
        else:
            sickbeard.searchQueueScheduler.action.unpause_backlog() #@UndefinedVariable

        redirect("/manage/manageSearches")

    @cherrypy.expose
    def forceVersionCheck(self):

        # force a check to see if there is a new version
        result = sickbeard.versionCheckScheduler.action.check_for_new_version(force=True) #@UndefinedVariable
        if result:
            logger.log(u"Forcing version check")

        redirect("/manage/manageSearches")

		
class Manage:

    manageSearches = ManageSearches()

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="manage.tmpl")
        t.submenu = ManageMenu
        return _munge(t)

    @cherrypy.expose
    def showEpisodeStatuses(self, tvdb_id, whichStatus):
        myDB = db.DBConnection()

        status_list = [int(whichStatus)]
        if status_list[0] == SNATCHED:
            status_list = Quality.SNATCHED + Quality.SNATCHED_PROPER
        
        cur_show_results = myDB.select("SELECT season, episode, name FROM tv_episodes WHERE showid = ? AND season != 0 AND status IN ("+','.join(['?']*len(status_list))+")", [int(tvdb_id)] + status_list)
        
        result = {}
        for cur_result in cur_show_results:
            cur_season = int(cur_result["season"])
            cur_episode = int(cur_result["episode"])
            
            if cur_season not in result:
                result[cur_season] = {}
            
            result[cur_season][cur_episode] = cur_result["name"]
        
        return json.dumps(result)

    @cherrypy.expose
    def episodeStatuses(self, whichStatus=None):

        if whichStatus:
            whichStatus = int(whichStatus)
            status_list = [whichStatus]
            if status_list[0] == SNATCHED:
                status_list = Quality.SNATCHED + Quality.SNATCHED_PROPER
        else:
            status_list = []
        
        t = PageTemplate(file="manage_episodeStatuses.tmpl")
        t.submenu = ManageMenu
        t.whichStatus = whichStatus

        # if we have no status then this is as far as we need to go
        if not status_list:
            return _munge(t)
        
        myDB = db.DBConnection()
        status_results = myDB.select("SELECT show_name, tv_shows.tvdb_id as tvdb_id FROM tv_episodes, tv_shows WHERE tv_episodes.status IN ("+','.join(['?']*len(status_list))+") AND season != 0 AND tv_episodes.showid = tv_shows.tvdb_id ORDER BY show_name", status_list)

        ep_counts = {}
        show_names = {}
        sorted_show_ids = []
        for cur_status_result in status_results:
            cur_tvdb_id = int(cur_status_result["tvdb_id"])
            if cur_tvdb_id not in ep_counts:
                ep_counts[cur_tvdb_id] = 1
            else:
                ep_counts[cur_tvdb_id] += 1
        
            show_names[cur_tvdb_id] = cur_status_result["show_name"]
            if cur_tvdb_id not in sorted_show_ids:
                sorted_show_ids.append(cur_tvdb_id)
        
        t.show_names = show_names
        t.ep_counts = ep_counts
        t.sorted_show_ids = sorted_show_ids
        return _munge(t)

    @cherrypy.expose
    def changeEpisodeStatuses(self, oldStatus, newStatus, *args, **kwargs):
        
        status_list = [int(oldStatus)]
        if status_list[0] == SNATCHED:
            status_list = Quality.SNATCHED + Quality.SNATCHED_PROPER

        to_change = {}
        
        # make a list of all shows and their associated args
        for arg in kwargs:
            tvdb_id, what = arg.split('-')
            
            # we don't care about unchecked checkboxes
            if kwargs[arg] != 'on':
                continue
            
            if tvdb_id not in to_change:
                to_change[tvdb_id] = []
            
            to_change[tvdb_id].append(what)
        
        myDB = db.DBConnection()

        for cur_tvdb_id in to_change:

            # get a list of all the eps we want to change if they just said "all"
            if 'all' in to_change[cur_tvdb_id]:
                all_eps_results = myDB.select("SELECT season, episode FROM tv_episodes WHERE status IN ("+','.join(['?']*len(status_list))+") AND season != 0 AND showid = ?", status_list + [cur_tvdb_id])
                all_eps = [str(x["season"])+'x'+str(x["episode"]) for x in all_eps_results]
                to_change[cur_tvdb_id] = all_eps

            Home().setStatus(cur_tvdb_id, '|'.join(to_change[cur_tvdb_id]), newStatus, direct=True)
            
        redirect('/manage/episodeStatuses')

    @cherrypy.expose
    def backlogShow(self, tvdb_id):
        
        show_obj = helpers.findCertainShow(sickbeard.showList, int(tvdb_id))
        
        if show_obj:
            sickbeard.backlogSearchScheduler.action.searchBacklog([show_obj]) #@UndefinedVariable
        
        redirect("/manage/backlogOverview")
        
    @cherrypy.expose
    def backlogOverview(self):

        t = PageTemplate(file="manage_backlogOverview.tmpl")
        t.submenu = ManageMenu

        myDB = db.DBConnection()

        showCounts = {}
        showCats = {}
        showSQLResults = {}

        for curShow in sickbeard.showList:

            epCounts = {}
            epCats = {}
            epCounts[Overview.SKIPPED] = 0
            epCounts[Overview.WANTED] = 0
            epCounts[Overview.QUAL] = 0
            epCounts[Overview.GOOD] = 0
            epCounts[Overview.UNAIRED] = 0

            sqlResults = myDB.select("SELECT * FROM tv_episodes WHERE showid = ? ORDER BY season*1000+episode DESC", [curShow.tvdbid])

            for curResult in sqlResults:

                curEpCat = curShow.getOverview(int(curResult["status"]))
                epCats[str(curResult["season"])+"x"+str(curResult["episode"])] = curEpCat
                epCounts[curEpCat] += 1

            showCounts[curShow.tvdbid] = epCounts
            showCats[curShow.tvdbid] = epCats
            showSQLResults[curShow.tvdbid] = sqlResults

        t.showCounts = showCounts
        t.showCats = showCats
        t.showSQLResults = showSQLResults

        return _munge(t)

    @cherrypy.expose
    def massEdit(self, toEdit=None):

        t = PageTemplate(file="manage_massEdit.tmpl")
        t.submenu = ManageMenu

        if not toEdit:
            redirect("/manage")

        showIDs = toEdit.split("|")
        showList = []
        for curID in showIDs:
            curID = int(curID)
            showObj = helpers.findCertainShow(sickbeard.showList, curID)
            if showObj:
                showList.append(showObj)

        season_folders_all_same = True
        last_season_folders = None

        paused_all_same = True
        last_paused = None

        quality_all_same = True
        last_quality = None

        root_dir_list = []

        for curShow in showList:
            
            cur_root_dir = ek.ek(os.path.dirname, curShow._location)
            if cur_root_dir not in root_dir_list:
                root_dir_list.append(cur_root_dir) 
            
            # if we know they're not all the same then no point even bothering
            if paused_all_same:
                # if we had a value already and this value is different then they're not all the same
                if last_paused not in (curShow.paused, None):
                    paused_all_same = False
                else:
                    last_paused = curShow.paused

            if season_folders_all_same:
                if last_season_folders not in (None, curShow.seasonfolders):
                    season_folders_all_same = False
                else:
                    last_season_folders = curShow.seasonfolders

            if quality_all_same:
                if last_quality not in (None, curShow.quality):
                    quality_all_same = False
                else:
                    last_quality = curShow.quality

        t.showList = toEdit
        t.paused_value = last_paused if paused_all_same else None
        t.season_folders_value = last_season_folders if season_folders_all_same else None
        t.quality_value = last_quality if quality_all_same else None
        t.root_dir_list = root_dir_list

        return _munge(t)

    @cherrypy.expose
    def massEditSubmit(self, paused=None, season_folders=None, quality_preset=False,
                       anyQualities=[], bestQualities=[], toEdit=None, *args, **kwargs):

        dir_map = {}
        for cur_arg in kwargs:
            if not cur_arg.startswith('orig_root_dir_'):
                continue
            which_index = cur_arg.replace('orig_root_dir_', '')
            end_dir = kwargs['new_root_dir_'+which_index]
            dir_map[kwargs[cur_arg]] = end_dir

        showIDs = toEdit.split("|")
        errors = []
        for curShow in showIDs:
            curErrors = []
            showObj = helpers.findCertainShow(sickbeard.showList, int(curShow))
            if not showObj:
                continue

            cur_root_dir = ek.ek(os.path.dirname, showObj._location)
            cur_show_dir = ek.ek(os.path.basename, showObj._location)
            if cur_root_dir in dir_map and cur_root_dir != dir_map[cur_root_dir]:
                new_show_dir = ek.ek(os.path.join, dir_map[cur_root_dir], cur_show_dir)
                logger.log(u"For show "+showObj.name+" changing dir from "+showObj._location+" to "+new_show_dir)
            else:
                new_show_dir = showObj._location
            
            if paused == 'keep':
                new_paused = showObj.paused
            else:
                new_paused = True if paused == 'enable' else False
            new_paused = 'on' if new_paused else 'off'

            if season_folders == 'keep':
                new_season_folders = showObj.seasonfolders
            else:
                new_season_folders = True if season_folders == 'enable' else False
            new_season_folders = 'on' if new_season_folders else 'off'

            if quality_preset == 'keep':
                anyQualities, bestQualities = Quality.splitQuality(showObj.quality)
            
            curErrors += Home().editShow(curShow, new_show_dir, anyQualities, bestQualities, new_season_folders, new_paused, directCall=True)

            if curErrors:
                logger.log(u"Errors: "+str(curErrors), logger.ERROR)
                errors.append('<b>%s:</b><br />\n<ul>' % showObj.name + '\n'.join(['<li>%s</li>' % error for error in curErrors]) + "</ul>")

        if len(errors) > 0:
            ui.notifications.error('%d error%s while saving changes:' % (len(errors), "" if len(errors) == 1 else "s"),
                        "<br />\n".join(errors))

        redirect("/manage")

    @cherrypy.expose
    def massUpdate(self, toUpdate=None, toRefresh=None, toRename=None, toDelete=None, toMetadata=None):

        if toUpdate != None:
            toUpdate = toUpdate.split('|')
        else:
            toUpdate = []

        if toRefresh != None:
            toRefresh = toRefresh.split('|')
        else:
            toRefresh = []

        if toRename != None:
            toRename = toRename.split('|')
        else:
            toRename = []

        if toDelete != None:
            toDelete = toDelete.split('|')
        else:
            toDelete = []

        if toMetadata != None:
            toMetadata = toMetadata.split('|')
        else:
            toMetadata = []

        errors = []
        refreshes = []
        updates = []
        renames = []

        for curShowID in set(toUpdate+toRefresh+toRename+toDelete+toMetadata):

            if curShowID == '':
                continue

            showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(curShowID))

            if showObj == None:
                continue

            if curShowID in toDelete:
                showObj.deleteShow()
                # don't do anything else if it's being deleted
                continue

            if curShowID in toUpdate:
                try:
                    sickbeard.showQueueScheduler.action.updateShow(showObj, True) #@UndefinedVariable
                    updates.append(showObj.name)
                except exceptions.CantUpdateException, e:
                    errors.append("Unable to update show "+showObj.name+": "+ex(e))

            # don't bother refreshing shows that were updated anyway
            if curShowID in toRefresh and curShowID not in toUpdate:
                try:
                    sickbeard.showQueueScheduler.action.refreshShow(showObj) #@UndefinedVariable
                    refreshes.append(showObj.name)
                except exceptions.CantRefreshException, e:
                    errors.append("Unable to refresh show "+showObj.name+": "+ex(e))

            if curShowID in toRename:
                sickbeard.showQueueScheduler.action.renameShowEpisodes(showObj) #@UndefinedVariable
                renames.append(showObj.name)

        if len(errors) > 0:
            ui.notifications.error("Errors encountered",
                        '<br >\n'.join(errors))

        messageDetail = ""

        if len(updates) > 0:
            messageDetail += "<br /><b>Updates</b><br /><ul><li>"
            messageDetail += "</li><li>".join(updates)
            messageDetail += "</li></ul>"

        if len(refreshes) > 0:
            messageDetail += "<br /><b>Refreshes</b><br /><ul><li>"
            messageDetail += "</li><li>".join(refreshes)
            messageDetail += "</li></ul>"

        if len(renames) > 0:
            messageDetail += "<br /><b>Renames</b><br /><ul><li>"
            messageDetail += "</li><li>".join(renames)
            messageDetail += "</li></ul>"

        if len(updates+refreshes+renames) > 0:
            ui.notifications.message("The following actions were queued:",
                          messageDetail)

        redirect("/manage")

