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

class HomePostProcess:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="home_postprocess.tmpl")
        t.submenu = HomeMenu()
        return _munge(t)

    @cherrypy.expose
    def processEpisode(self, dir=None, nzbName=None, jobName=None, quiet=None):

        if dir == None:
            redirect("/home/postprocess")
        else:
            result = processTV.processDir(dir, nzbName)
            if quiet != None and int(quiet) == 1:
                return result

            result = result.replace("\n","<br />\n")
            return _genericMessage("Postprocessing results", result)


class NewHomeAddShows:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="home_addShows.tmpl")
        t.submenu = HomeMenu()
        return _munge(t)

    @cherrypy.expose
    def getTVDBLanguages(self):
        result = tvdb_api.Tvdb().config['valid_languages']

        # Make sure list is sorted alphabetically but 'en' is in front
        if 'en' in result:
            del result[result.index('en')]
        result.sort()
        result.insert(0,'en')

        return json.dumps({'results': result})

    @cherrypy.expose
    def sanitizeFileName(self, name):
        return helpers.sanitizeFileName(name)

    @cherrypy.expose
    def searchTVDBForShowName(self, name, lang="en"):
        if not lang or lang == 'null':
                lang = "en"

        baseURL = "http://thetvdb.com/api/GetSeries.php?"

        params = {'seriesname': name.encode('utf-8'),
                  'language': lang}

        finalURL = baseURL + urllib.urlencode(params)

        urlData = helpers.getURL(finalURL)

        try:
            seriesXML = etree.ElementTree(etree.XML(urlData))
        except Exception, e:
            logger.log(u"Unable to parse XML for some reason: "+ex(e)+" from XML: "+urlData, logger.ERROR)
            return ''

        series = seriesXML.getiterator('Series')

        results = []

        for curSeries in series:
            results.append((int(curSeries.findtext('seriesid')), curSeries.findtext('SeriesName'), curSeries.findtext('FirstAired')))

        lang_id = tvdb_api.Tvdb().config['langabbv_to_id'][lang]

        return json.dumps({'results': results, 'langid': lang_id})

    @cherrypy.expose
    def massAddTable(self, rootDir=None):
        t = PageTemplate(file="home_massAddTable.tmpl")
        t.submenu = HomeMenu()
        
        myDB = db.DBConnection()

        if not rootDir:
            return "No folders selected." 
        elif type(rootDir) != list:
            root_dirs = [rootDir]
        else:
            root_dirs = rootDir
        
        root_dirs = [urllib.unquote_plus(x) for x in root_dirs]

        default_index = int(sickbeard.ROOT_DIRS.split('|')[0])
        if len(root_dirs) > default_index:
            tmp = root_dirs[default_index]
            if tmp in root_dirs:
                root_dirs.remove(tmp)
                root_dirs = [tmp]+root_dirs
        
        dir_list = []
        
        for root_dir in root_dirs:
            try:
                file_list = ek.ek(os.listdir, root_dir)
            except:
                continue

            for cur_file in file_list:

                cur_path = ek.ek(os.path.normpath, ek.ek(os.path.join, root_dir, cur_file))
                if not ek.ek(os.path.isdir, cur_path):
                    continue
                
                cur_dir = {
                           'dir': cur_path,
                           'display_dir': '<b>'+ek.ek(os.path.dirname, cur_path)+os.sep+'</b>'+ek.ek(os.path.basename, cur_path),
                           }
                
                # see if the folder is in XBMC already
                dirResults = myDB.select("SELECT * FROM tv_shows WHERE location = ?", [cur_path])
                
                if dirResults:
                    cur_dir['added_already'] = True
                else:
                    cur_dir['added_already'] = False
                
                dir_list.append(cur_dir)
                
                tvdb_id = ''
                show_name = ''
                for cur_provider in sickbeard.metadata_provider_dict.values():
                    (tvdb_id, show_name) = cur_provider.retrieveShowMetadata(cur_path)
                    if tvdb_id and show_name:
                        break
                
                cur_dir['existing_info'] = (tvdb_id, show_name)
                
                if tvdb_id and helpers.findCertainShow(sickbeard.showList, tvdb_id):
                    cur_dir['added_already'] = True 

        t.dirList = dir_list
        
        return _munge(t)

    @cherrypy.expose
    def newShow(self, show_to_add=None, other_shows=None):
        """
        Display the new show page which collects a tvdb id, folder, and extra options and
        posts them to addNewShow
        """
        t = PageTemplate(file="home_newShow.tmpl")
        t.submenu = HomeMenu()
        
        show_dir, tvdb_id, show_name = self.split_extra_show(show_to_add)
        
        if tvdb_id and show_name:
            use_provided_info = True
        else:
            use_provided_info = False
        
        # tell the template whether we're giving it show name & TVDB ID
        t.use_provided_info = use_provided_info
        
        # use the given show_dir for the tvdb search if available 
        if not show_dir:
            t.default_show_name = ''
        elif not show_name:
            t.default_show_name = ek.ek(os.path.basename, ek.ek(os.path.normpath, show_dir)).replace('.',' ')
        else:
            t.default_show_name = show_name
        
        # carry a list of other dirs if given
        if not other_shows:
            other_shows = []
        elif type(other_shows) != list:
            other_shows = [other_shows]
        
        if use_provided_info:
            t.provided_tvdb_id = tvdb_id
            t.provided_tvdb_name = show_name
            
        t.provided_show_dir = show_dir
        t.other_shows = other_shows
        
        return _munge(t)

    @cherrypy.expose
    def addNewShow(self, whichSeries=None, tvdbLang="en", rootDir=None, defaultStatus=None,
                   anyQualities=None, bestQualities=None, seasonFolders=None, fullShowPath=None,
                   other_shows=None, skipShow=None):
        """
        Receive tvdb id, dir, and other options and create a show from them. If extra show dirs are
        provided then it forwards back to newShow, if not it goes to /home.
        """
        
        # grab our list of other dirs if given
        if not other_shows:
            other_shows = []
        elif type(other_shows) != list:
            other_shows = [other_shows]
            
        def finishAddShow(): 
            # if there are no extra shows then go home
            if not other_shows:
                redirect('/home')
            
            # peel off the next one
            next_show_dir = other_shows[0]
            rest_of_show_dirs = other_shows[1:]
            
            # go to add the next show
            return self.newShow(next_show_dir, rest_of_show_dirs)
        
        # if we're skipping then behave accordingly
        if skipShow:
            return finishAddShow()
        
        # sanity check on our inputs
        if (not rootDir and not fullShowPath) or not whichSeries:
            return "Missing params, no tvdb id or folder:"+repr(whichSeries)+" and "+repr(rootDir)+"/"+repr(fullShowPath)
        
        # figure out what show we're adding and where
        series_pieces = whichSeries.partition('|')
        if len(series_pieces) < 3:
            return "Error with show selection."
        
        tvdb_id = int(series_pieces[0])
        show_name = series_pieces[2]
        
        # use the whole path if it's given, or else append the show name to the root dir to get the full show path
        if fullShowPath:
            show_dir = ek.ek(os.path.normpath, fullShowPath)
        else:
            show_dir = ek.ek(os.path.join, rootDir, helpers.sanitizeFileName(show_name))
        
        # blanket policy - if the dir exists you should have used "add existing show" numbnuts
        if ek.ek(os.path.isdir, show_dir) and not fullShowPath:
            ui.notifications.error("Unable to add show", "Folder "+show_dir+" exists already")
            redirect('/home')
        
        # create the dir and make sure it worked
        dir_exists = helpers.makeDir(show_dir)
        if not dir_exists:
            logger.log(u"Unable to create the folder "+show_dir+", can't add the show", logger.ERROR)
            ui.notifications.error("Unable to add show", "Unable to create the folder "+show_dir+", can't add the show")
            redirect("/home")
        else:
            helpers.chmodAsParent(show_dir)

        # prepare the inputs for passing along
        if seasonFolders == "on":
            seasonFolders = 1
        else:
            seasonFolders = 0
        
        if not anyQualities:
            anyQualities = []
        if not bestQualities:
            bestQualities = []
        if type(anyQualities) != list:
            anyQualities = [anyQualities]
        if type(bestQualities) != list:
            bestQualities = [bestQualities]
        newQuality = Quality.combineQualities(map(int, anyQualities), map(int, bestQualities))
        
        # add the show
        sickbeard.showQueueScheduler.action.addShow(tvdb_id, show_dir, int(defaultStatus), newQuality, seasonFolders, tvdbLang) #@UndefinedVariable
        ui.notifications.message('Show added', 'Adding the specified show into '+show_dir)

        return finishAddShow()
        

    @cherrypy.expose
    def existingShows(self):
        """
        Prints out the page to add existing shows from a root dir 
        """
        t = PageTemplate(file="home_addExistingShow.tmpl")
        t.submenu = HomeMenu()
        
        return _munge(t)

    def split_extra_show(self, extra_show):
        if not extra_show:
            return (None, None, None)
        split_vals = extra_show.split('|')
        if len(split_vals) < 3:
            return (extra_show, None, None)
        show_dir = split_vals[0]
        tvdb_id = split_vals[1]
        show_name = '|'.join(split_vals[2:])
        
        return (show_dir, tvdb_id, show_name)

    @cherrypy.expose
    def addExistingShows(self, shows_to_add=None, promptForSettings=None):
        """
        Receives a dir list and add them. Adds the ones with given TVDB IDs first, then forwards
        along to the newShow page.
        """

        # grab a list of other shows to add, if provided
        if not shows_to_add:
            shows_to_add = []
        elif type(shows_to_add) != list:
            shows_to_add = [shows_to_add]
        
        shows_to_add = [urllib.unquote_plus(x) for x in shows_to_add]
        
        if promptForSettings == "on":
            promptForSettings = 1
        else:
            promptForSettings = 0
        
        tvdb_id_given = []
        dirs_only = []
        # separate all the ones with TVDB IDs
        for cur_dir in shows_to_add:
            if not '|' in cur_dir:
                dirs_only.append(cur_dir)
            else:
                show_dir, tvdb_id, show_name = self.split_extra_show(cur_dir)
                if not show_dir or not tvdb_id or not show_name:
                    continue
                tvdb_id_given.append((show_dir, int(tvdb_id), show_name))


        # if they want me to prompt for settings then I will just carry on to the newShow page
        if promptForSettings and shows_to_add:
            return self.newShow(shows_to_add[0], shows_to_add[1:])
        
        # if they don't want me to prompt for settings then I can just add all the nfo shows now
        num_added = 0
        for cur_show in tvdb_id_given:
            show_dir, tvdb_id, show_name = cur_show

            # add the show
            sickbeard.showQueueScheduler.action.addShow(tvdb_id, show_dir, SKIPPED, sickbeard.QUALITY_DEFAULT, sickbeard.SEASON_FOLDERS_DEFAULT) #@UndefinedVariable
            num_added += 1
         
        if num_added:
            ui.notifications.message("Shows Added", "Automatically added "+str(num_added)+" from their existing metadata files")

        # if we're done then go home
        if not dirs_only:
            redirect('/home')

        # for the remaining shows we need to prompt for each one, so forward this on to the newShow page
        return self.newShow(dirs_only[0], dirs_only[1:])




ErrorLogsMenu = [
    { 'title': 'Clear Errors', 'path': 'errorlogs/clearerrors' },
    #{ 'title': 'View Log',  'path': 'errorlogs/viewlog'  },
]


class ErrorLogs:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="errorlogs.tmpl")
        t.submenu = ErrorLogsMenu

        return _munge(t)


    @cherrypy.expose
    def clearerrors(self):
        classes.ErrorViewer.clear()
        redirect("/errorlogs")

    @cherrypy.expose
    def viewlog(self, minLevel=logger.MESSAGE, maxLines=500):

        t = PageTemplate(file="viewlogs.tmpl")
        t.submenu = ErrorLogsMenu

        minLevel = int(minLevel)

        data = []
        if os.path.isfile(logger.sb_log_instance.log_file):
            f = ek.ek(open, logger.sb_log_instance.log_file)
            data = f.readlines()
            f.close()

        regex =  "^(\w{3})\-(\d\d)\s*(\d\d)\:(\d\d):(\d\d)\s*([A-Z]+)\s*(.+?)\s*\:\:\s*(.*)$"

        finalData = []

        numLines = 0
        lastLine = False
        numToShow = min(maxLines, len(data))

        for x in reversed(data):

            x = x.decode('utf-8')
            match = re.match(regex, x)

            if match:
                level = match.group(6)
                if level not in logger.reverseNames:
                    lastLine = False
                    continue

                if logger.reverseNames[level] >= minLevel:
                    lastLine = True
                    finalData.append(x)
                else:
                    lastLine = False
                    continue

            elif lastLine:
                finalData.append("AA"+x)

            numLines += 1

            if numLines >= numToShow:
                break

        result = "".join(finalData)

        t.logLines = result
        t.minLevel = minLevel

        return _munge(t)

from sickbeard.webserve_manage import *
from sickbeard.webserve_home import *
from sickbeard.webserve_history import *
from sickbeard.webserve_config import *
from sickbeard.webserve import _munge, redirect, _genericMessage, _getEpisode

class Home:
    
    @cherrypy.expose
    def is_alive(self):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        if sickbeard.started:
            return str(sickbeard.PID)
        else:
            return "nope"

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="home.tmpl")
        t.submenu = HomeMenu()
        return _munge(t)

    addShows = NewHomeAddShows()

    postprocess = HomePostProcess()

    @cherrypy.expose
    def testGrowl(self, host=None, password=None):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.growl_notifier.test_notify(host, password)
        if password==None or password=='':
            pw_append = ''
        else:
            pw_append = " with password: " + password

        if result:
            return "Registered and Tested growl successfully "+urllib.unquote_plus(host)+pw_append
        else:
            return "Registration and Testing of growl failed "+urllib.unquote_plus(host)+pw_append

    @cherrypy.expose
    def testProwl(self, prowl_api=None, prowl_priority=0):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.prowl_notifier.test_notify(prowl_api, prowl_priority)
        if result:
            return "Test prowl notice sent successfully"
        else:
            return "Test prowl notice failed"

    @cherrypy.expose
    def testNotifo(self, username=None, apisecret=None):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.notifo_notifier.test_notify(username, apisecret)
        if result:
            return "Notifo notification succeeded. Check your Notifo clients to make sure it worked"
        else:
            return "Error sending Notifo notification"

    @cherrypy.expose
    def twitterStep1(self):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        return notifiers.twitter_notifier._get_authorization()

    @cherrypy.expose
    def twitterStep2(self, key):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.twitter_notifier._get_credentials(key)
        logger.log(u"result: "+str(result))
        if result:
            return "Key verification successful"
        else:
            return "Unable to verify key"

    @cherrypy.expose
    def testTwitter(self):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.twitter_notifier.test_notify()
        if result:
            return "Tweet successful, check your twitter to make sure it worked"
        else:
            return "Error sending tweet"

    @cherrypy.expose
    def testXBMC(self, host=None, username=None, password=None):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.xbmc_notifier.test_notify(urllib.unquote_plus(host), username, password)
        if result:
            return "Test notice sent successfully to "+urllib.unquote_plus(host)
        else:
            return "Test notice failed to "+urllib.unquote_plus(host)

    @cherrypy.expose
    def testPLEX(self, host=None, username=None, password=None):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.plex_notifier.test_notify(urllib.unquote_plus(host), username, password)
        if result:
            return "Test notice sent successfully to "+urllib.unquote_plus(host)
        else:
            return "Test notice failed to "+urllib.unquote_plus(host)

    @cherrypy.expose
    def testLibnotify(self):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        if notifiers.libnotify_notifier.test_notify():
            return "Tried sending desktop notification via libnotify"
        else:
            return notifiers.libnotify.diagnose()

    @cherrypy.expose
    def testNMJ(self, host=None, database=None, mount=None):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.nmj_notifier.test_notify(urllib.unquote_plus(host), database, mount)
        if result:
            return "Successfull started the scan update"
        else:
            return "Test failed to start the scan update"

    @cherrypy.expose
    def settingsNMJ(self, host=None):
        cherrypy.response.headers['Cache-Control'] = "max-age=0,no-cache,no-store"

        result = notifiers.nmj_notifier.notify_settings(urllib.unquote_plus(host))
        if result:
            return '{"message": "Got settings from %(host)s", "database": "%(database)s", "mount": "%(mount)s"}' % {"host": host, "database": sickbeard.NMJ_DATABASE, "mount": sickbeard.NMJ_MOUNT}
        else:
            return '{"message": "Failed! Make sure your Popcorn is on and NMJ is running. (see Log & Errors -> Debug for detailed info)", "database": "", "mount": ""}'


    @cherrypy.expose
    def shutdown(self):

        threading.Timer(2, sickbeard.invoke_shutdown).start()

        title = "Shutting down"
        message = "Sick Beard is shutting down..."

        return _genericMessage(title, message)

    @cherrypy.expose
    def restart(self, pid=None):

        if str(pid) != str(sickbeard.PID):
            redirect("/home")

        t = PageTemplate(file="restart.tmpl")
        t.submenu = HomeMenu()

        # do a soft restart
        threading.Timer(2, sickbeard.invoke_restart, [False]).start()

        return _munge(t)

    @cherrypy.expose
    def update(self, pid=None):

        if str(pid) != str(sickbeard.PID):
            redirect("/home")

        updated = sickbeard.versionCheckScheduler.action.update() #@UndefinedVariable

        if updated:
            # do a hard restart
            threading.Timer(2, sickbeard.invoke_restart, [False]).start()
            t = PageTemplate(file="restart_bare.tmpl")
            return _munge(t)
        else:
            return _genericMessage("Update Failed","Update wasn't successful, not restarting. Check your log for more information.")

    @cherrypy.expose
    
    def export_shows(self):
        
        myDB = db.DBConnection()
        sqlResults = myDB.select("SELECT * FROM tv_shows")
        print "-----------------"
    @cherrypy.expose
    
    def displayShow(self, show=None):

        if show == None:
            return _genericMessage("Error", "Invalid show ID")
        else:
            showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

            if showObj == None:

                return _genericMessage("Error", "Unable to find the specified show.")

        myDB = db.DBConnection()

        seasonResults = myDB.select(
            "SELECT DISTINCT season FROM tv_episodes WHERE showid = ? ORDER BY season desc",
            [showObj.tvdbid]
        )

        sqlResults = myDB.select(
            "SELECT * FROM tv_episodes WHERE showid = ? ORDER BY season*1000+episode DESC",
            [showObj.tvdbid]
        )

        t = PageTemplate(file="displayShow.tmpl")
        t.submenu = [ { 'title': 'Edit', 'path': 'home/editShow?show=%d'%showObj.tvdbid } ]

        try:
            t.showLoc = (showObj.location, True)
        except sickbeard.exceptions.ShowDirNotFoundException:
            t.showLoc = (showObj._location, False)

        show_message = ''

        if sickbeard.showQueueScheduler.action.isBeingAdded(showObj): #@UndefinedVariable
            show_message = 'This show is in the process of being downloaded from theTVDB.com - the info below is incomplete.'

        elif sickbeard.showQueueScheduler.action.isBeingUpdated(showObj): #@UndefinedVariable
            show_message = 'The information below is in the process of being updated.'

        elif sickbeard.showQueueScheduler.action.isBeingRefreshed(showObj): #@UndefinedVariable
            show_message = 'The episodes below are currently being refreshed from disk'

        elif sickbeard.showQueueScheduler.action.isInRefreshQueue(showObj): #@UndefinedVariable
            show_message = 'This show is queued to be refreshed.'

        elif sickbeard.showQueueScheduler.action.isInUpdateQueue(showObj): #@UndefinedVariable
            show_message = 'This show is queued and awaiting an update.'

        if not sickbeard.showQueueScheduler.action.isBeingAdded(showObj): #@UndefinedVariable
            if not sickbeard.showQueueScheduler.action.isBeingUpdated(showObj): #@UndefinedVariable
                t.submenu.append({ 'title': 'Delete',               'path': 'home/deleteShow?show=%d'%showObj.tvdbid, 'confirm': True })
                t.submenu.append({ 'title': 'Re-scan files',        'path': 'home/refreshShow?show=%d'%showObj.tvdbid })
                t.submenu.append({ 'title': 'Force Full Update',    'path': 'home/updateShow?show=%d&amp;force=1'%showObj.tvdbid })
                t.submenu.append({ 'title': 'Update show in XBMC',  'path': 'home/updateXBMC?showName=%s'%urllib.quote_plus(showObj.name.encode('utf-8')), 'requires': haveXBMC })
                t.submenu.append({ 'title': 'Rename Episodes',      'path': 'home/fixEpisodeNames?show=%d'%showObj.tvdbid, 'confirm': True })

        t.show = showObj
        t.sqlResults = sqlResults
        t.seasonResults = seasonResults
        t.show_message = show_message

        epCounts = {}
        epCats = {}
        epCounts[Overview.SKIPPED] = 0
        epCounts[Overview.WANTED] = 0
        epCounts[Overview.QUAL] = 0
        epCounts[Overview.GOOD] = 0
        epCounts[Overview.UNAIRED] = 0

        for curResult in sqlResults:

            curEpCat = showObj.getOverview(int(curResult["status"]))
            epCats[str(curResult["season"])+"x"+str(curResult["episode"])] = curEpCat
            epCounts[curEpCat] += 1

        def titler(x):
            if not x:
                return x
            if x.lower().startswith('a '):
                    x = x[2:]
            elif x.lower().startswith('the '):
                    x = x[4:]
            return x
        t.sortedShowList = sorted(sickbeard.showList, lambda x, y: cmp(titler(x.name), titler(y.name)))

        t.epCounts = epCounts
        t.epCats = epCats

        return _munge(t)
		
    @cherrypy.expose
    def exportShowEpisodeList(self, show=None):

        if show == None:
            return _genericMessage("Error", "Invalid show ID")
        else:
            showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

            if showObj == None:

                return _genericMessage("Error", "Unable to find the specified show.")

        myDB = db.DBConnection()


        sqlResults = myDB.select(
            "SELECT * FROM tv_episodes WHERE showid = ? ORDER BY season*1000+episode DESC",
            [showObj.tvdbid]
        )
        print "\n\n---------------------------\n"
        import unicodedata
        def conv(str): return unicodedata.normalize('NFKD', str).encode('ascii','ignore')
        print "Name, Season, Description, Airdate"
        for r in sqlResults:
            print "{}, {}, \"{}\"".format(conv(r["name"]), r["season"], conv(r["description"][0:50]))
        
        print "\n\n---------------------------\n"

        return _genericMessage("Notice", "Show list printed to console/log")


		
		
    @cherrypy.expose
    def plotDetails(self, show, season, episode):
        result = db.DBConnection().action("SELECT description FROM tv_episodes WHERE showid = ? AND season = ? AND episode = ?", (show, season, episode)).fetchone()
        return result['description'] if result else 'Episode not found.'

    @cherrypy.expose
    def editShow(self, show=None, location=None, anyQualities=[], bestQualities=[], seasonfolders=None, paused=None, directCall=False, air_by_date=None, tvdbLang=None):

        if show == None:
            errString = "Invalid show ID: "+str(show)
            if directCall:
                return [errString]
            else:
                return _genericMessage("Error", errString)

        showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            errString = "Unable to find the specified show: "+str(show)
            if directCall:
                return [errString]
            else:
                return _genericMessage("Error", errString)

        if not location and not anyQualities and not bestQualities and not seasonfolders:

            t = PageTemplate(file="editShow.tmpl")
            t.submenu = HomeMenu()
            with showObj.lock:
                t.show = showObj

            return _munge(t)

        if seasonfolders == "on":
            seasonfolders = 1
        else:
            seasonfolders = 0

        if paused == "on":
            paused = 1
        else:
            paused = 0

        if air_by_date == "on":
            air_by_date = 1
        else:
            air_by_date = 0

        if tvdbLang and tvdbLang in tvdb_api.Tvdb().config['valid_languages']:
            tvdb_lang = tvdbLang
        else:
            tvdb_lang = showObj.lang

        # if we changed the language then kick off an update
        if tvdb_lang == showObj.lang:
            do_update = False
        else:
            do_update = True

        if type(anyQualities) != list:
            anyQualities = [anyQualities]

        if type(bestQualities) != list:
            bestQualities = [bestQualities]

        errors = []
        with showObj.lock:
            newQuality = Quality.combineQualities(map(int, anyQualities), map(int, bestQualities))
            showObj.quality = newQuality

            if bool(showObj.seasonfolders) != bool(seasonfolders):
                showObj.seasonfolders = seasonfolders
                try:
                    sickbeard.showQueueScheduler.action.refreshShow(showObj) #@UndefinedVariable
                except exceptions.CantRefreshException, e:
                    errors.append("Unable to refresh this show: "+ex(e))

            showObj.paused = paused
            showObj.air_by_date = air_by_date
            showObj.lang = tvdb_lang

            # if we change location clear the db of episodes, change it, write to db, and rescan
            if os.path.normpath(showObj._location) != os.path.normpath(location):
                logger.log(os.path.normpath(showObj._location)+" != "+os.path.normpath(location))
                if not ek.ek(os.path.isdir, location):
                    errors.append("New location <tt>%s</tt> does not exist" % location)

                # don't bother if we're going to update anyway
                elif not do_update:
                    # change it
                    try:
                        showObj.location = location
                        try:
                            sickbeard.showQueueScheduler.action.refreshShow(showObj) #@UndefinedVariable
                        except exceptions.CantRefreshException, e:
                            errors.append("Unable to refresh this show:"+ex(e))
                        # grab updated info from TVDB
                        #showObj.loadEpisodesFromTVDB()
                        # rescan the episodes in the new folder
                    except exceptions.NoNFOException:
                        errors.append("The folder at <tt>%s</tt> doesn't contain a tvshow.nfo - copy your files to that folder before you change the directory in Sick Beard." % location)

            # save it to the DB
            showObj.saveToDB()

        # force the update
        if do_update:
            try:
                sickbeard.showQueueScheduler.action.updateShow(showObj, True) #@UndefinedVariable
                time.sleep(1)
            except exceptions.CantUpdateException, e:
                errors.append("Unable to force an update on the show.")

        if directCall:
            return errors

        if len(errors) > 0:
            ui.notifications.error('%d error%s while saving changes:' % (len(errors), "" if len(errors) == 1 else "s"),
                        '<ul>' + '\n'.join(['<li>%s</li>' % error for error in errors]) + "</ul>")

        redirect("/home/displayShow?show=" + show)

    @cherrypy.expose
    def deleteShow(self, show=None):

        if show == None:
            return _genericMessage("Error", "Invalid show ID")

        showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            return _genericMessage("Error", "Unable to find the specified show")

        if sickbeard.showQueueScheduler.action.isBeingAdded(showObj) or sickbeard.showQueueScheduler.action.isBeingUpdated(showObj): #@UndefinedVariable
            return _genericMessage("Error", "Shows can't be deleted while they're being added or updated.")

        showObj.deleteShow()

        ui.notifications.message('<b>%s</b> has been deleted' % showObj.name)
        redirect("/home")

    @cherrypy.expose
    def refreshShow(self, show=None):

        if show == None:
            return _genericMessage("Error", "Invalid show ID")

        showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            return _genericMessage("Error", "Unable to find the specified show")

        # force the update from the DB
        try:
            sickbeard.showQueueScheduler.action.refreshShow(showObj) #@UndefinedVariable
        except exceptions.CantRefreshException, e:
            ui.notifications.error("Unable to refresh this show.",
                        ex(e))

        time.sleep(3)

        redirect("/home/displayShow?show="+str(showObj.tvdbid))

    @cherrypy.expose
    def updateShow(self, show=None, force=0):

        if show == None:
            return _genericMessage("Error", "Invalid show ID")

        showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            return _genericMessage("Error", "Unable to find the specified show")

        # force the update
        try:
            sickbeard.showQueueScheduler.action.updateShow(showObj, bool(force)) #@UndefinedVariable
        except exceptions.CantUpdateException, e:
            ui.notifications.error("Unable to update this show.",
                        ex(e))

        # just give it some time
        time.sleep(3)

        redirect("/home/displayShow?show="+str(showObj.tvdbid))


    @cherrypy.expose
    def updateXBMC(self, showName=None):

        for curHost in [x.strip() for x in sickbeard.XBMC_HOST.split(",")]:
            if notifiers.xbmc_notifier._update_library(curHost, showName=showName):
                ui.notifications.message("Command sent to XBMC host " + curHost + " to update library")
            else:
                ui.notifications.error("Unable to contact XBMC host " + curHost)
        redirect('/home')


    @cherrypy.expose
    def updatePLEX(self):

        if notifiers.plex_notifier._update_library():
            ui.notifications.message("Command sent to Plex Media Server host " + sickbeard.PLEX_HOST + " to update library")
            logger.log(u"Plex library update initiated for host " + sickbeard.PLEX_HOST, logger.DEBUG)
        else:
            ui.notifications.error("Unable to contact Plex Media Server host " + sickbeard.PLEX_HOST)
            logger.log(u"Plex library update failed for host " + sickbeard.PLEX_HOST, logger.ERROR)
        redirect('/home')


    @cherrypy.expose
    def fixEpisodeNames(self, show=None):

        if show == None:
            return _genericMessage("Error", "Invalid show ID")

        showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            return _genericMessage("Error", "Unable to find the specified show")

        if sickbeard.showQueueScheduler.action.isBeingAdded(showObj): #@UndefinedVariable
            return _genericMessage("Error", "Show is still being added, wait until it is finished before you rename files")

        showObj.fixEpisodeNames()

        redirect("/home/displayShow?show=" + show)

    @cherrypy.expose
    def setStatus(self, show=None, eps=None, status=None, direct=False):

        if show == None or eps == None or status == None:
            errMsg = "You must specify a show and at least one episode"
            if direct:
                ui.notifications.error('Error', errMsg)
                return json.dumps({'result': 'error'})
            else:
                return _genericMessage("Error", errMsg)

        if not statusStrings.has_key(int(status)):
            errMsg = "Invalid status"
            if direct:
                ui.notifications.error('Error', errMsg)
                return json.dumps({'result': 'error'})
            else:
                return _genericMessage("Error", errMsg)

        showObj = sickbeard.helpers.findCertainShow(sickbeard.showList, int(show))

        if showObj == None:
            errMsg = "Error", "Show not in show list"
            if direct:
                ui.notifications.error('Error', errMsg)
                return json.dumps({'result': 'error'})
            else:
                return _genericMessage("Error", errMsg)

        segment_list = []

        if eps != None:

            for curEp in eps.split('|'):

                logger.log(u"Attempting to set status on episode "+curEp+" to "+status, logger.DEBUG)

                epInfo = curEp.split('x')

                epObj = showObj.getEpisode(int(epInfo[0]), int(epInfo[1]))

                if int(status) == WANTED:
                    # figure out what segment the episode is in and remember it so we can backlog it
                    if epObj.show.air_by_date:
                        ep_segment = str(epObj.airdate)[:7]
                    else:
                        ep_segment = epObj.season
    
                    if ep_segment not in segment_list:
                        segment_list.append(ep_segment)

                if epObj == None:
                    return _genericMessage("Error", "Episode couldn't be retrieved")

                with epObj.lock:
                    # don't let them mess up UNAIRED episodes
                    if epObj.status == UNAIRED:
                        logger.log(u"Refusing to change status of "+curEp+" because it is UNAIRED", logger.ERROR)
                        continue

                    if int(status) in Quality.DOWNLOADED and epObj.status not in Quality.SNATCHED + Quality.SNATCHED_PROPER + Quality.DOWNLOADED + [IGNORED] and not ek.ek(os.path.isfile, epObj.location):
                        logger.log(u"Refusing to change status of "+curEp+" to DOWNLOADED because it's not SNATCHED/DOWNLOADED", logger.ERROR)
                        continue

                    epObj.status = int(status)
                    epObj.saveToDB()

        msg = "Backlog was automatically started for the following seasons of <b>"+showObj.name+"</b>:<br />"
        for cur_segment in segment_list:
            msg += "<li>Season "+str(cur_segment)+"</li>"
            logger.log(u"Sending backlog for "+showObj.name+" season "+str(cur_segment)+" because some eps were set to wanted")
            cur_backlog_queue_item = search_queue.BacklogQueueItem(showObj, cur_segment)
            sickbeard.searchQueueScheduler.action.add_item(cur_backlog_queue_item) #@UndefinedVariable
        msg += "</ul>"

        if segment_list:
            ui.notifications.message("Backlog started", msg)

        if direct:
            return json.dumps({'result': 'success'})
        else:
            redirect("/home/displayShow?show=" + show)

    @cherrypy.expose
    def searchEpisode(self, show=None, season=None, episode=None):

        # retrieve the episode object and fail if we can't get one 
        ep_obj = _getEpisode(show, season, episode)
        if isinstance(ep_obj, str):
            return json.dumps({'result': 'failure'})

        # make a queue item for it and put it on the queue
        ep_queue_item = search_queue.ManualSearchQueueItem(ep_obj)
        sickbeard.searchQueueScheduler.action.add_item(ep_queue_item) #@UndefinedVariable

        # wait until the queue item tells us whether it worked or not
        while ep_queue_item.success == None: #@UndefinedVariable
            time.sleep(1)

        # return the correct json value
        if ep_queue_item.success:
            return json.dumps({'result': statusStrings[ep_obj.status]})

        return json.dumps({'result': 'failure'})

