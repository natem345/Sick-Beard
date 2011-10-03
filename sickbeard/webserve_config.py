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


ConfigMenu = [
    { 'title': 'General',           'path': 'config/general/'          },
    { 'title': 'Search Settings',   'path': 'config/search/'           },
    { 'title': 'Search Providers',  'path': 'config/providers/'        },
    { 'title': 'Post Processing',   'path': 'config/postProcessing/'   },
    { 'title': 'Notifications',     'path': 'config/notifications/'    },
]

class ConfigGeneral:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="config_general.tmpl")
        t.submenu = ConfigMenu
        return _munge(t)

    @cherrypy.expose
    def saveRootDirs(self, rootDirString=None):
        sickbeard.ROOT_DIRS = rootDirString
    
    @cherrypy.expose
    def saveAddShowDefaults(self, defaultSeasonFolders, defaultStatus, anyQualities, bestQualities):

        if anyQualities:
            anyQualities = anyQualities.split(',')
        else:
            anyQualities = []

        if bestQualities:
            bestQualities = bestQualities.split(',')
        else:
            bestQualities = []

        newQuality = Quality.combineQualities(map(int, anyQualities), map(int, bestQualities))
        
        sickbeard.STATUS_DEFAULT = int(defaultStatus)
        sickbeard.QUALITY_DEFAULT = int(newQuality)

        if defaultSeasonFolders == "true":
            defaultSeasonFolders = 1
        else:
            defaultSeasonFolders = 0

        sickbeard.SEASON_FOLDERS_DEFAULT = int(defaultSeasonFolders)

    
    @cherrypy.expose
    def saveGeneral(self, log_dir=None, web_port=None, web_log=None, web_ipv6=None,
                    launch_browser=None, web_username=None,
                    web_password=None, version_notify=None):

        results = []

        if web_ipv6 == "on":
            web_ipv6 = 1
        else:
            web_ipv6 = 0

        if web_log == "on":
            web_log = 1
        else:
            web_log = 0

        if launch_browser == "on":
            launch_browser = 1
        else:
            launch_browser = 0

        if version_notify == "on":
            version_notify = 1
        else:
            version_notify = 0

        if not config.change_LOG_DIR(log_dir):
            results += ["Unable to create directory " + os.path.normpath(log_dir) + ", log dir not changed."]

        sickbeard.LAUNCH_BROWSER = launch_browser

        sickbeard.WEB_PORT = int(web_port)
        sickbeard.WEB_IPV6 = web_ipv6
        sickbeard.WEB_LOG = web_log
        sickbeard.WEB_USERNAME = web_username
        sickbeard.WEB_PASSWORD = web_password

        config.change_VERSION_NOTIFY(version_notify)

        sickbeard.save_config()

        if len(results) > 0:
            for x in results:
                logger.log(x, logger.ERROR)
            ui.notifications.error('Error(s) Saving Configuration',
                        '<br />\n'.join(results))
        else:
            ui.notifications.message('Configuration Saved', ek.ek(os.path.join, sickbeard.CONFIG_FILE) )

        redirect("/config/general/")


class ConfigSearch:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="config_search.tmpl")
        t.submenu = ConfigMenu
        return _munge(t)

    @cherrypy.expose
    def saveSearch(self, use_nzbs=None, use_torrents=None, nzb_dir=None, sab_username=None, sab_password=None,
                       sab_apikey=None, sab_category=None, sab_host=None, nzbget_password=None, nzbget_category=None, nzbget_host=None,
                       torrent_dir=None, nzb_method=None, usenet_retention=None, search_frequency=None, download_propers=None):

        results = []

        if not config.change_NZB_DIR(nzb_dir):
            results += ["Unable to create directory " + os.path.normpath(nzb_dir) + ", dir not changed."]

        if not config.change_TORRENT_DIR(torrent_dir):
            results += ["Unable to create directory " + os.path.normpath(torrent_dir) + ", dir not changed."]

        config.change_SEARCH_FREQUENCY(search_frequency)

        if download_propers == "on":
            download_propers = 1
        else:
            download_propers = 0

        if use_nzbs == "on":
            use_nzbs = 1
        else:
            use_nzbs = 0

        if use_torrents == "on":
            use_torrents = 1
        else:
            use_torrents = 0

        if usenet_retention == None:
            usenet_retention = 200

        sickbeard.USE_NZBS = use_nzbs
        sickbeard.USE_TORRENTS = use_torrents

        sickbeard.NZB_METHOD = nzb_method
        sickbeard.USENET_RETENTION = int(usenet_retention)

        sickbeard.DOWNLOAD_PROPERS = download_propers

        sickbeard.SAB_USERNAME = sab_username
        sickbeard.SAB_PASSWORD = sab_password
        sickbeard.SAB_APIKEY = sab_apikey.strip()
        sickbeard.SAB_CATEGORY = sab_category

        if sab_host and not re.match('https?://.*', sab_host):
            sab_host = 'http://' + sab_host

        if not sab_host.endswith('/'):
            sab_host = sab_host + '/'

        sickbeard.SAB_HOST = sab_host

        sickbeard.NZBGET_PASSWORD = nzbget_password
        sickbeard.NZBGET_CATEGORY = nzbget_category
        sickbeard.NZBGET_HOST = nzbget_host


        sickbeard.save_config()

        if len(results) > 0:
            for x in results:
                logger.log(x, logger.ERROR)
            ui.notifications.error('Error(s) Saving Configuration',
                        '<br />\n'.join(results))
        else:
            ui.notifications.message('Configuration Saved', ek.ek(os.path.join, sickbeard.CONFIG_FILE) )

        redirect("/config/search/")

class ConfigPostProcessing:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="config_postProcessing.tmpl")
        t.submenu = ConfigMenu
        return _munge(t)

    @cherrypy.expose
    def savePostProcessing(self, season_folders_format=None, naming_show_name=None, naming_ep_type=None,
                    naming_multi_ep_type=None, naming_ep_name=None, naming_use_periods=None,
                    naming_sep_type=None, naming_quality=None, naming_dates=None,
                    xbmc_data=None, mediabrowser_data=None, sony_ps3_data=None, wdtv_data=None, tivo_data=None,
                    use_banner=None, keep_processed_dir=None, process_automatically=None, rename_episodes=None,
                    move_associated_files=None, tv_download_dir=None):

        results = []

        if not config.change_TV_DOWNLOAD_DIR(tv_download_dir):
            results += ["Unable to create directory " + os.path.normpath(tv_download_dir) + ", dir not changed."]

        if naming_show_name == "on":
            naming_show_name = 1
        else:
            naming_show_name = 0

        if naming_ep_name == "on":
            naming_ep_name = 1
        else:
            naming_ep_name = 0

        if naming_use_periods == "on":
            naming_use_periods = 1
        else:
            naming_use_periods = 0

        if naming_quality == "on":
            naming_quality = 1
        else:
            naming_quality = 0

        if naming_dates == "on":
            naming_dates = 1
        else:
            naming_dates = 0

        if use_banner == "on":
            use_banner = 1
        else:
            use_banner = 0

        if process_automatically == "on":
            process_automatically = 1
        else:
            process_automatically = 0

        if rename_episodes == "on":
            rename_episodes = 1
        else:
            rename_episodes = 0

        if keep_processed_dir == "on":
            keep_processed_dir = 1
        else:
            keep_processed_dir = 0

        if move_associated_files == "on":
            move_associated_files = 1
        else:
            move_associated_files = 0

        sickbeard.PROCESS_AUTOMATICALLY = process_automatically
        sickbeard.KEEP_PROCESSED_DIR = keep_processed_dir
        sickbeard.RENAME_EPISODES = rename_episodes
        sickbeard.MOVE_ASSOCIATED_FILES = move_associated_files

        sickbeard.metadata_provider_dict['XBMC'].set_config(xbmc_data)
        sickbeard.metadata_provider_dict['MediaBrowser'].set_config(mediabrowser_data)
        sickbeard.metadata_provider_dict['Sony PS3'].set_config(sony_ps3_data)
        sickbeard.metadata_provider_dict['WDTV'].set_config(wdtv_data)
        sickbeard.metadata_provider_dict['TIVO'].set_config(tivo_data)
        
        sickbeard.SEASON_FOLDERS_FORMAT = season_folders_format

        sickbeard.NAMING_SHOW_NAME = naming_show_name
        sickbeard.NAMING_EP_NAME = naming_ep_name
        sickbeard.NAMING_USE_PERIODS = naming_use_periods
        sickbeard.NAMING_QUALITY = naming_quality
        sickbeard.NAMING_DATES = naming_dates
        sickbeard.NAMING_EP_TYPE = int(naming_ep_type)
        sickbeard.NAMING_MULTI_EP_TYPE = int(naming_multi_ep_type)
        sickbeard.NAMING_SEP_TYPE = int(naming_sep_type)

        sickbeard.USE_BANNER = use_banner

        sickbeard.save_config()

        if len(results) > 0:
            for x in results:
                logger.log(x, logger.ERROR)
            ui.notifications.error('Error(s) Saving Configuration',
                        '<br />\n'.join(results))
        else:
            ui.notifications.message('Configuration Saved', ek.ek(os.path.join, sickbeard.CONFIG_FILE) )

        redirect("/config/postProcessing/")

    @cherrypy.expose
    def testNaming(self, show_name=None, ep_type=None, multi_ep_type=None, ep_name=None,
                   sep_type=None, use_periods=None, quality=None, whichTest="single"):

        if show_name == None:
            show_name = sickbeard.NAMING_SHOW_NAME
        else:
            if show_name == "0":
                show_name = False
            else:
                show_name = True

        if ep_name == None:
            ep_name = sickbeard.NAMING_EP_NAME
        else:
            if ep_name == "0":
                ep_name = False
            else:
                ep_name = True

        if use_periods == None:
            use_periods = sickbeard.NAMING_USE_PERIODS
        else:
            if use_periods == "0":
                use_periods = False
            else:
                use_periods = True

        if quality == None:
            quality = sickbeard.NAMING_QUALITY
        else:
            if quality == "0":
                quality = False
            else:
                quality = True

        if ep_type == None:
            ep_type = sickbeard.NAMING_EP_TYPE
        else:
            ep_type = int(ep_type)

        if multi_ep_type == None:
            multi_ep_type = sickbeard.NAMING_MULTI_EP_TYPE
        else:
            multi_ep_type = int(multi_ep_type)

        if sep_type == None:
            sep_type = sickbeard.NAMING_SEP_TYPE
        else:
            sep_type = int(sep_type)

        class TVShow():
            def __init__(self):
                self.name = "Show Name"
                self.genre = "Comedy"
                self.air_by_date = 0

        # fake a TVShow (hack since new TVShow is coming anyway)
        class TVEpisode(tv.TVEpisode):
            def __init__(self, season, episode, name):
                self.relatedEps = []
                self._name = name
                self._season = season
                self._episode = episode
                self.show = TVShow()


        # make a fake episode object
        ep = TVEpisode(1,2,"Ep Name")
        ep._status = Quality.compositeStatus(DOWNLOADED, Quality.HDTV)

        if whichTest == "multi":
            ep._name = "Ep Name (1)"
            secondEp = TVEpisode(1,3,"Ep Name (2)")
            ep.relatedEps.append(secondEp)

        # get the name
        name = ep.prettyName(show_name, ep_type, multi_ep_type, ep_name, sep_type, use_periods, quality)

        return name

class ConfigProviders:

    @cherrypy.expose
    def index(self):
        t = PageTemplate(file="config_providers.tmpl")
        t.submenu = ConfigMenu
        return _munge(t)

    @cherrypy.expose
    def canAddNewznabProvider(self, name):

        if not name:
            return json.dumps({'error': 'Invalid name specified'})

        providerDict = dict(zip([x.getID() for x in sickbeard.newznabProviderList], sickbeard.newznabProviderList))

        tempProvider = newznab.NewznabProvider(name, '')

        if tempProvider.getID() in providerDict:
            return json.dumps({'error': 'Exists as '+providerDict[tempProvider.getID()].name})
        else:
            return json.dumps({'success': tempProvider.getID()})

    @cherrypy.expose
    def saveNewznabProvider(self, name, url, key=''):

        if not name or not url:
            return '0'

        if not url.endswith('/'):
            url = url + '/'

        providerDict = dict(zip([x.name for x in sickbeard.newznabProviderList], sickbeard.newznabProviderList))

        if name in providerDict:
            if not providerDict[name].default:
                providerDict[name].name = name
                providerDict[name].url = url
            providerDict[name].key = key

            return providerDict[name].getID() + '|' + providerDict[name].configStr()

        else:

            newProvider = newznab.NewznabProvider(name, url, key)
            sickbeard.newznabProviderList.append(newProvider)
            return newProvider.getID() + '|' + newProvider.configStr()



    @cherrypy.expose
    def deleteNewznabProvider(self, id):

        providerDict = dict(zip([x.getID() for x in sickbeard.newznabProviderList], sickbeard.newznabProviderList))

        if id not in providerDict or providerDict[id].default:
            return '0'

        # delete it from the list
        sickbeard.newznabProviderList.remove(providerDict[id])

        if id in sickbeard.PROVIDER_ORDER:
            sickbeard.PROVIDER_ORDER.remove(id)

        return '1'


    @cherrypy.expose
    def saveProviders(self, nzbs_org_uid=None, nzbs_org_hash=None,
                      nzbmatrix_username=None, nzbmatrix_apikey=None,
                      nzbs_r_us_uid=None, nzbs_r_us_hash=None, newznab_string=None,
                      tvtorrents_digest=None, tvtorrents_hash=None, 
                      newzbin_username=None, newzbin_password=None,
                      provider_order=None):

        results = []

        provider_str_list = provider_order.split()
        provider_list = []

        newznabProviderDict = dict(zip([x.getID() for x in sickbeard.newznabProviderList], sickbeard.newznabProviderList))

        finishedNames = []

        # add all the newznab info we got into our list
        for curNewznabProviderStr in newznab_string.split('!!!'):

            if not curNewznabProviderStr:
                continue

            curName, curURL, curKey = curNewznabProviderStr.split('|')

            newProvider = newznab.NewznabProvider(curName, curURL, curKey)

            curID = newProvider.getID()

            # if it already exists then update it
            if curID in newznabProviderDict:
                newznabProviderDict[curID].name = curName
                newznabProviderDict[curID].url = curURL
                newznabProviderDict[curID].key = curKey
            else:
                sickbeard.newznabProviderList.append(newProvider)

            finishedNames.append(curID)


        # delete anything that is missing
        for curProvider in sickbeard.newznabProviderList:
            if curProvider.getID() not in finishedNames:
                sickbeard.newznabProviderList.remove(curProvider)

        # do the enable/disable
        for curProviderStr in provider_str_list:
            curProvider, curEnabled = curProviderStr.split(':')
            curEnabled = int(curEnabled)

            provider_list.append(curProvider)

            if curProvider == 'nzbs_org':
                sickbeard.NZBS = curEnabled
            elif curProvider == 'nzbs_r_us':
                sickbeard.NZBSRUS = curEnabled
            elif curProvider == 'nzbmatrix':
                sickbeard.NZBMATRIX = curEnabled
            elif curProvider == 'newzbin':
                sickbeard.NEWZBIN = curEnabled
            elif curProvider == 'bin_req':
                sickbeard.BINREQ = curEnabled
            elif curProvider == 'womble_s_index':
                sickbeard.WOMBLE = curEnabled
            elif curProvider == 'ezrss':
                sickbeard.EZRSS = curEnabled
            elif curProvider == 'tvtorrents':
                sickbeard.TVTORRENTS = curEnabled
            elif curProvider in newznabProviderDict:
                newznabProviderDict[curProvider].enabled = bool(curEnabled)
            else:
                logger.log(u"don't know what "+curProvider+" is, skipping")

        sickbeard.TVTORRENTS_DIGEST = tvtorrents_digest.strip()
        sickbeard.TVTORRENTS_HASH = tvtorrents_hash.strip()

        sickbeard.NZBS_UID = nzbs_org_uid.strip()
        sickbeard.NZBS_HASH = nzbs_org_hash.strip()

        sickbeard.NZBSRUS_UID = nzbs_r_us_uid.strip()
        sickbeard.NZBSRUS_HASH = nzbs_r_us_hash.strip()

        sickbeard.NZBMATRIX_USERNAME = nzbmatrix_username
        sickbeard.NZBMATRIX_APIKEY = nzbmatrix_apikey.strip()

        sickbeard.NEWZBIN_USERNAME = newzbin_username
        sickbeard.NEWZBIN_PASSWORD = newzbin_password

        sickbeard.PROVIDER_ORDER = provider_list

        sickbeard.save_config()

        if len(results) > 0:
            for x in results:
                logger.log(x, logger.ERROR)
            ui.notifications.error('Error(s) Saving Configuration',
                        '<br />\n'.join(results))
        else:
            ui.notifications.message('Configuration Saved', ek.ek(os.path.join, sickbeard.CONFIG_FILE) )

        redirect("/config/providers/")

class ConfigNotifications:

    @cherrypy.expose
    def index(self):
        t = PageTemplate(file="config_notifications.tmpl")
        t.submenu = ConfigMenu
        return _munge(t)

    @cherrypy.expose
    def saveNotifications(self, use_xbmc=None, xbmc_notify_onsnatch=None, xbmc_notify_ondownload=None,
                          xbmc_update_library=None, xbmc_update_full=None, xbmc_host=None, xbmc_username=None, xbmc_password=None,
                          use_plex=None, plex_notify_onsnatch=None, plex_notify_ondownload=None, plex_update_library=None,
                          plex_server_host=None, plex_host=None, plex_username=None, plex_password=None,
                          use_growl=None, growl_notify_onsnatch=None, growl_notify_ondownload=None, growl_host=None, growl_password=None, 
                          use_prowl=None, prowl_notify_onsnatch=None, prowl_notify_ondownload=None, prowl_api=None, prowl_priority=0, 
                          use_twitter=None, twitter_notify_onsnatch=None, twitter_notify_ondownload=None, 
                          use_notifo=None, notifo_notify_onsnatch=None, notifo_notify_ondownload=None, notifo_username=None, notifo_apisecret=None,
                          use_libnotify=None, libnotify_notify_onsnatch=None, libnotify_notify_ondownload=None,
                          use_nmj=None, nmj_host=None, nmj_database=None, nmj_mount=None, use_synoindex=None):

        results = []

        if xbmc_notify_onsnatch == "on":
            xbmc_notify_onsnatch = 1
        else:
            xbmc_notify_onsnatch = 0

        if xbmc_notify_ondownload == "on":
            xbmc_notify_ondownload = 1
        else:
            xbmc_notify_ondownload = 0

        if xbmc_update_library == "on":
            xbmc_update_library = 1
        else:
            xbmc_update_library = 0

        if xbmc_update_full == "on":
            xbmc_update_full = 1
        else:
            xbmc_update_full = 0

        if use_xbmc == "on":
            use_xbmc = 1
        else:
            use_xbmc = 0

        if plex_update_library == "on":
            plex_update_library = 1
        else:
            plex_update_library = 0

        if plex_notify_onsnatch == "on":
            plex_notify_onsnatch = 1
        else:
            plex_notify_onsnatch = 0

        if plex_notify_ondownload == "on":
            plex_notify_ondownload = 1
        else:
            plex_notify_ondownload = 0

        if use_plex == "on":
            use_plex = 1
        else:
            use_plex = 0

        if growl_notify_onsnatch == "on":
            growl_notify_onsnatch = 1
        else:
            growl_notify_onsnatch = 0

        if growl_notify_ondownload == "on":
            growl_notify_ondownload = 1
        else:
            growl_notify_ondownload = 0

        if use_growl == "on":
            use_growl = 1
        else:
            use_growl = 0
            
        if prowl_notify_onsnatch == "on":
            prowl_notify_onsnatch = 1
        else:
            prowl_notify_onsnatch = 0

        if prowl_notify_ondownload == "on":
            prowl_notify_ondownload = 1
        else:
            prowl_notify_ondownload = 0
        if use_prowl == "on":
            use_prowl = 1
        else:
            use_prowl = 0

        if twitter_notify_onsnatch == "on":
            twitter_notify_onsnatch = 1
        else:
            twitter_notify_onsnatch = 0

        if twitter_notify_ondownload == "on":
            twitter_notify_ondownload = 1
        else:
            twitter_notify_ondownload = 0
        if use_twitter == "on":
            use_twitter = 1
        else:
            use_twitter = 0

        if notifo_notify_onsnatch == "on":
            notifo_notify_onsnatch = 1
        else:
            notifo_notify_onsnatch = 0

        if notifo_notify_ondownload == "on":
            notifo_notify_ondownload = 1
        else:
            notifo_notify_ondownload = 0
        if use_notifo == "on":
            use_notifo = 1
        else:
            use_notifo = 0

        if use_nmj == "on":
            use_nmj = 1
        else:
            use_nmj = 0

        if use_synoindex == "on":
            use_synoindex = 1
        else:
            use_synoindex = 0

        sickbeard.USE_XBMC = use_xbmc
        sickbeard.XBMC_NOTIFY_ONSNATCH = xbmc_notify_onsnatch
        sickbeard.XBMC_NOTIFY_ONDOWNLOAD = xbmc_notify_ondownload
        sickbeard.XBMC_UPDATE_LIBRARY = xbmc_update_library
        sickbeard.XBMC_UPDATE_FULL = xbmc_update_full
        sickbeard.XBMC_HOST = xbmc_host
        sickbeard.XBMC_USERNAME = xbmc_username
        sickbeard.XBMC_PASSWORD = xbmc_password

        sickbeard.USE_PLEX = use_plex
        sickbeard.PLEX_NOTIFY_ONSNATCH = plex_notify_onsnatch
        sickbeard.PLEX_NOTIFY_ONDOWNLOAD = plex_notify_ondownload
        sickbeard.PLEX_UPDATE_LIBRARY = plex_update_library
        sickbeard.PLEX_HOST = plex_host
        sickbeard.PLEX_SERVER_HOST = plex_server_host
        sickbeard.PLEX_USERNAME = plex_username
        sickbeard.PLEX_PASSWORD = plex_password

        sickbeard.USE_GROWL = use_growl
        sickbeard.GROWL_NOTIFY_ONSNATCH = growl_notify_onsnatch
        sickbeard.GROWL_NOTIFY_ONDOWNLOAD = growl_notify_ondownload
        sickbeard.GROWL_HOST = growl_host
        sickbeard.GROWL_PASSWORD = growl_password

        sickbeard.USE_PROWL = use_prowl
        sickbeard.PROWL_NOTIFY_ONSNATCH = prowl_notify_onsnatch
        sickbeard.PROWL_NOTIFY_ONDOWNLOAD = prowl_notify_ondownload
        sickbeard.PROWL_API = prowl_api
        sickbeard.PROWL_PRIORITY = prowl_priority

        sickbeard.USE_TWITTER = use_twitter
        sickbeard.TWITTER_NOTIFY_ONSNATCH = twitter_notify_onsnatch
        sickbeard.TWITTER_NOTIFY_ONDOWNLOAD = twitter_notify_ondownload

        sickbeard.USE_NOTIFO = use_notifo
        sickbeard.NOTIFO_NOTIFY_ONSNATCH = notifo_notify_onsnatch
        sickbeard.NOTIFO_NOTIFY_ONDOWNLOAD = notifo_notify_ondownload
        sickbeard.NOTIFO_USERNAME = notifo_username
        sickbeard.NOTIFO_APISECRET = notifo_apisecret

        sickbeard.USE_LIBNOTIFY = use_libnotify == "on"
        sickbeard.LIBNOTIFY_NOTIFY_ONSNATCH = libnotify_notify_onsnatch == "on"
        sickbeard.LIBNOTIFY_NOTIFY_ONDOWNLOAD = libnotify_notify_ondownload == "on"

        sickbeard.USE_NMJ = use_nmj
        sickbeard.NMJ_HOST = nmj_host
        sickbeard.NMJ_DATABASE = nmj_database
        sickbeard.NMJ_MOUNT = nmj_mount

        sickbeard.USE_SYNOINDEX = use_synoindex

        sickbeard.save_config()

        if len(results) > 0:
            for x in results:
                logger.log(x, logger.ERROR)
            ui.notifications.error('Error(s) Saving Configuration',
                        '<br />\n'.join(results))
        else:
            ui.notifications.message('Configuration Saved', ek.ek(os.path.join, sickbeard.CONFIG_FILE) )

        redirect("/config/notifications/")


class Config:

    @cherrypy.expose
    def index(self):

        t = PageTemplate(file="config.tmpl")
        t.submenu = ConfigMenu
        return _munge(t)

    general = ConfigGeneral()

    search = ConfigSearch()
    
    postProcessing = ConfigPostProcessing()

    providers = ConfigProviders()

    notifications = ConfigNotifications()

def haveXBMC():
    return sickbeard.XBMC_HOST

def havePLEX():
    return sickbeard.PLEX_SERVER_HOST

def HomeMenu():
    return [
        { 'title': 'Add Shows',              'path': 'home/addShows/',                                          },
        { 'title': 'Manual Post-Processing', 'path': 'home/postprocess/'                                        },
        { 'title': 'Update XBMC',            'path': 'home/updateXBMC/', 'requires': haveXBMC                   },
        { 'title': 'Update Plex',            'path': 'home/updatePLEX/', 'requires': havePLEX                   },
        { 'title': 'Restart',                'path': 'home/restart/?pid='+str(sickbeard.PID), 'confirm': True   },
        { 'title': 'Shutdown',               'path': 'home/shutdown/', 'confirm': True                          },
    ]
