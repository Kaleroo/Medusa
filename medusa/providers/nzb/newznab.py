# coding=utf-8

"""Provider code for Newznab provider."""

from __future__ import unicode_literals

import logging
import os
import re
import time
import traceback

from medusa import (
    app,
    tv,
)
from medusa.bs4_parser import BS4Parser
from medusa.common import cpu_presets
from medusa.helper.common import (
    convert_size,
    try_int,
)
from medusa.helper.encoding import ss
from medusa.indexers.indexer_config import (
    INDEXER_TMDB,
    INDEXER_TVDBV2,
    INDEXER_TVMAZE,
    mappings,
)
from medusa.logger.adapters.style import BraceAdapter
from medusa.providers.nzb.nzb_provider import NZBProvider

from requests.compat import urljoin
import validators

log = BraceAdapter(logging.getLogger(__name__))
log.logger.addHandler(logging.NullHandler())


class NewznabProvider(NZBProvider):
    """
    Generic provider for built in and custom providers who expose a newznab compatible api.

    Tested with: newznab, nzedb, spotweb, torznab
    """

    def __init__(self, name, url, key='0', cat_ids='5030,5040', search_mode='eponly',
                 search_fallback=False, enable_daily=True, enable_backlog=False, enable_manualsearch=False):
        """Initialize the class."""
        super(NewznabProvider, self).__init__(name)

        self.url = url
        self.key = key

        self.search_mode = search_mode
        self.search_fallback = search_fallback
        self.enable_daily = enable_daily
        self.enable_manualsearch = enable_manualsearch
        self.enable_backlog = enable_backlog

        # 0 in the key spot indicates that no key is needed
        self.needs_auth = self.key != '0'
        self.public = not self.needs_auth

        self.cat_ids = cat_ids if cat_ids else '5030,5040'

        self.torznab = False

        self.default = False

        self.caps = False
        self.cap_tv_search = None
        self.force_query = False
        self.providers_without_caps = ['gingadaddy', '6box']
        # self.cap_search = None
        # self.cap_movie_search = None
        # self.cap_audio_search = None

        self.cache = tv.Cache(self)

    def search(self, search_strings, age=0, ep_obj=None):
        """
        Search indexer using the params in search_strings, either for latest releases, or a string/id search.

        :return: list of results in dict form
        """
        results = []
        if not self._check_auth():
            return results

        # For providers that don't have caps, or for which the t=caps is not working.
        if not self.caps and all(provider not in self.url for provider in self.providers_without_caps):
            self.get_newznab_categories(just_caps=True)
            if not self.caps:
                return results

        for mode in search_strings:
            self.torznab = False
            search_params = {
                't': 'search',
                'limit': 100,
                'offset': 0,
                'cat': self.cat_ids.strip(', ') or '5030,5040',
                'maxage': app.USENET_RETENTION
            }

            if self.needs_auth and self.key:
                search_params['apikey'] = self.key

            if mode != 'RSS':
                match_indexer = self._match_indexer()
                search_params['t'] = 'tvsearch' if match_indexer and not self.force_query else 'search'

                if search_params['t'] == 'tvsearch':
                    search_params.update(match_indexer)

                    if ep_obj.series.air_by_date or ep_obj.series.sports:
                        date_str = str(ep_obj.airdate)
                        search_params['season'] = date_str.partition('-')[0]
                        search_params['ep'] = date_str.partition('-')[2].replace('-', '/')
                    else:
                        search_params['season'] = ep_obj.scene_season
                        search_params['ep'] = ep_obj.scene_episode

                if mode == 'Season':
                    search_params.pop('ep', '')

            items = []
            log.debug('Search mode: {0}', mode)

            for search_string in search_strings[mode]:

                if mode != 'RSS':
                    # If its a PROPER search, need to change param to 'search' so it searches using 'q' param
                    if any(proper_string in search_string for proper_string in self.proper_strings):
                        search_params['t'] = 'search'

                    log.debug(
                        'Search show using {search}', {
                            'search': 'search string: {search_string}'.format(
                                search_string=search_string if search_params['t'] != 'tvsearch' else 'indexer_id: {indexer_id}'.format(indexer_id=match_indexer)
                            )
                        }
                    )

                    if search_params['t'] != 'tvsearch':
                        search_params['q'] = search_string

                time.sleep(cpu_presets[app.CPU_PRESET])

                response = self.session.get(urljoin(self.url, 'api'), params=search_params)
                if not response or not response.text:
                    log.debug('No data returned from provider')
                    continue

                with BS4Parser(response.text, 'html5lib') as html:
                    if not self._check_auth_from_data(html):
                        return items

                    try:
                        self.torznab = 'xmlns:torznab' in html.rss.attrs
                    except AttributeError:
                        self.torznab = False

                    if not html('item'):
                        log.debug('No results returned from provider. Check chosen Newznab search categories'
                                  ' in provider settings and/or usenet retention')
                        continue

                    for item in html('item'):
                        try:
                            title = item.title.get_text(strip=True)
                            download_url = None
                            if item.link:
                                if validators.url(item.link.get_text(strip=True)):
                                    download_url = item.link.get_text(strip=True)
                                elif validators.url(item.link.next.strip()):
                                    download_url = item.link.next.strip()

                            if not download_url and item.enclosure:
                                if validators.url(item.enclosure.get('url', '').strip()):
                                    download_url = item.enclosure.get('url', '').strip()

                            if not (title and download_url):
                                continue

                            seeders = leechers = -1
                            if 'gingadaddy' in self.url:
                                size_regex = re.search(r'\d*.?\d* [KMGT]B', str(item.description))
                                item_size = size_regex.group() if size_regex else -1
                            else:
                                item_size = item.size.get_text(strip=True) if item.size else -1
                                for attr in item('newznab:attr') + item('torznab:attr'):
                                    item_size = attr['value'] if attr['name'] == 'size' else item_size
                                    seeders = try_int(attr['value']) if attr['name'] == 'seeders' else seeders
                                    peers = try_int(attr['value']) if attr['name'] == 'peers' else None
                                    leechers = peers - seeders if peers else leechers

                            if not item_size or (self.torznab and (seeders is -1 or leechers is -1)):
                                continue

                            size = convert_size(item_size) or -1

                            pubdate_raw = item.pubdate.get_text(strip=True)
                            pubdate = self.parse_pubdate(pubdate_raw)

                            item = {
                                'title': title,
                                'link': download_url,
                                'size': size,
                                'seeders': seeders,
                                'leechers': leechers,
                                'pubdate': pubdate,
                            }
                            if mode != 'RSS':
                                if seeders == -1:
                                    log.debug('Found result: {0}', title)
                                else:
                                    log.debug('Found result: {0} with {1} seeders and {2} leechers',
                                              title, seeders, leechers)

                            items.append(item)
                        except (AttributeError, TypeError, KeyError, ValueError, IndexError):
                            log.error('Failed parsing provider. Traceback: {0!r}',
                                      traceback.format_exc())
                            continue

                # Since we arent using the search string,
                # break out of the search string loop
                if 'tvdbid' in search_params:
                    break

            results += items

        # Reproces but now use force_query = True
        if not results and not self.force_query:
            self.force_query = True
            return self.search(search_strings, ep_obj=ep_obj)

        return results

    def _check_auth(self):
        """
        Check that user has set their api key if it is needed.

        :return: True/False
        """
        if self.needs_auth and not self.key:
            log.warning('Invalid api key. Check your settings')
            return False

        return True

    def _check_auth_from_data(self, data):
        """
        Check that the returned data is valid.

        :return: _check_auth if valid otherwise False if there is an error
        """
        if data('categories') + data('item'):
            return self._check_auth()

        try:
            err_desc = data.error.attrs['description']
            if not err_desc:
                raise Exception
        except (AttributeError, TypeError):
            return self._check_auth()

        log.info(ss(err_desc))

        return False

    def _get_size(self, item):
        """
        Get size info from a result item.

        Returns int size or -1
        """
        return try_int(item.get('size', -1), -1)

    def config_string(self):
        """Generate a '|' delimited string of instance attributes, for saving to config.ini."""
        return '|'.join([
            self.name, self.url, self.key, self.cat_ids, str(int(self.enabled)),
            self.search_mode, str(int(self.search_fallback)),
            str(int(self.enable_daily)), str(int(self.enable_backlog)), str(int(self.enable_manualsearch))
        ])

    @staticmethod
    def get_providers_list(data):
        """Return list of nzb providers."""
        default_list = [
            provider for provider in
            (NewznabProvider._make_provider(x) for x in NewznabProvider._get_default_providers().split('!!!'))
            if provider]

        providers_list = [
            provider for provider in
            (NewznabProvider._make_provider(x) for x in data.split('!!!'))
            if provider]

        seen_values = set()
        providers_set = []

        for provider in providers_list:
            value = provider.name

            if value not in seen_values:
                providers_set.append(provider)
                seen_values.add(value)

        providers_list = providers_set
        providers_dict = dict(zip([provider.name for provider in providers_list], providers_list))

        for default in default_list:
            if not default:
                continue

            if default.name not in providers_dict:
                default.default = True
                providers_list.append(default)
            else:
                providers_dict[default.name].default = True
                providers_dict[default.name].name = default.name
                providers_dict[default.name].url = default.url
                providers_dict[default.name].needs_auth = default.needs_auth
                providers_dict[default.name].search_mode = default.search_mode
                providers_dict[default.name].search_fallback = default.search_fallback
                providers_dict[default.name].enable_daily = default.enable_daily
                providers_dict[default.name].enable_backlog = default.enable_backlog
                providers_dict[default.name].enable_manualsearch = default.enable_manualsearch

        return [provider for provider in providers_list if provider]

    def image_name(self):
        """
        Check if we have an image for this provider already.

        Returns found image or the default newznab image
        """
        if os.path.isfile(os.path.join(app.PROG_DIR, 'static/images/providers/', self.get_id() + '.png')):
            return self.get_id() + '.png'
        return 'newznab.png'

    def _match_indexer(self):
        """Use the indexers id and externals, and return the most optimal indexer with value.

        For newznab providers we prefer to use tvdb for searches, but if this is not available for shows that have
        been indexed using an alternative indexer, we could also try other indexers id's that are available
        and supported by this newznab provider.
        """
        # The following mapping should map the newznab capabilities to our indexers or externals in indexer_config.
        map_caps = {INDEXER_TMDB: 'tmdbid', INDEXER_TVDBV2: 'tvdbid', INDEXER_TVMAZE: 'tvmazeid'}

        return_mapping = {}

        if not self.show:
            # If we don't have show, can't get tvdbid
            return return_mapping

        if not self.cap_tv_search or self.cap_tv_search == 'True':
            # We didn't get back a supportedParams, lets return, and continue with doing a search string search.
            return return_mapping

        for search_type in self.cap_tv_search.split(','):
            if search_type == 'tvdbid' and self._get_tvdb_id():
                return_mapping['tvdbid'] = self._get_tvdb_id()
                # If we got a tvdb we're satisfied, we don't need to look for other capabilities.
                if return_mapping['tvdbid']:
                    return return_mapping
            else:
                # Move to the configured capability / indexer mappings. To see if we can get a match.
                for map_indexer in map_caps:
                    if map_caps[map_indexer] == search_type:
                        if self.show.indexer == map_indexer:
                            # We have a direct match on the indexer used, no need to try the externals.
                            return_mapping[map_caps[map_indexer]] = self.show.indexerid
                            return return_mapping
                        elif self.show.externals.get(mappings[map_indexer]):
                            # No direct match, let's see if one of the externals provides a valid search_type.
                            mapped_external_indexer = self.show.externals.get(mappings[map_indexer])
                            if mapped_external_indexer:
                                return_mapping[map_caps[map_indexer]] = mapped_external_indexer

        return return_mapping

    @staticmethod
    def _make_provider(config):
        if not config:
            return None

        try:
            values = config.split('|')
            # Pad values with None for each missing value
            values.extend([None for x in range(len(values), 10)])

            (name, url, key, category_ids, enabled,
             search_mode, search_fallback,
             enable_daily, enable_backlog, enable_manualsearch
             ) = values

        except ValueError:
            log.error('Skipping Newznab provider string: {config!r}, incorrect format',
                      {'config': config})
            return None

        new_provider = NewznabProvider(
            name, url, key=key, cat_ids=category_ids,
            search_mode=search_mode or 'eponly',
            search_fallback=search_fallback or 0,
            enable_daily=enable_daily or 0,
            enable_backlog=enable_backlog or 0,
            enable_manualsearch=enable_manualsearch or 0)
        new_provider.enabled = enabled == '1'

        return new_provider

    def set_caps(self, data):
        """Set caps."""
        if not data:
            return

        def _parse_cap(tag):
            elm = data.find(tag)
            return elm.get('supportedparams', 'True') if elm and elm.get('available') else ''

        self.cap_tv_search = _parse_cap('tv-search')
        # self.cap_search = _parse_cap('search')
        # self.cap_movie_search = _parse_cap('movie-search')
        # self.cap_audio_search = _parse_cap('audio-search')

        # self.caps = any([self.cap_tv_search, self.cap_search, self.cap_movie_search, self.cap_audio_search])
        self.caps = any([self.cap_tv_search])

    def get_newznab_categories(self, just_caps=False):
        """
        Use the newznab provider url and apikey to get the capabilities.

        Makes use of the default newznab caps param. e.a. http://yournewznab/api?t=caps&apikey=skdfiw7823sdkdsfjsfk
        Returns a tuple with (succes or not, array with dicts [{'id': '5070', 'name': 'Anime'},
        {'id': '5080', 'name': 'Documentary'}, {'id': '5020', 'name': 'Foreign'}...etc}], error message)
        """
        return_categories = []

        if not self._check_auth():
            return False, return_categories, 'Provider requires auth and your key is not set'

        url_params = {'t': 'caps'}
        if self.needs_auth and self.key:
            url_params['apikey'] = self.key

        response = self.session.get(urljoin(self.url, 'api'), params=url_params)
        if not response or not response.text:
            error_string = 'Error getting caps xml for [{0}]'.format(self.name)
            log.warning(error_string)
            return False, return_categories, error_string

        with BS4Parser(response.text, 'html5lib') as html:
            if not html.find('categories'):
                error_string = 'Error parsing caps xml for [{0}]'.format(self.name)
                log.debug(error_string)
                return False, return_categories, error_string

            self.set_caps(html.find('searching'))
            if just_caps:
                return

            for category in html('category'):
                if 'TV' in category.get('name', '') and category.get('id', ''):
                    return_categories.append({'id': category['id'], 'name': category['name']})
                    for subcat in category('subcat'):
                        if subcat.get('name', '') and subcat.get('id', ''):
                            return_categories.append({'id': subcat['id'], 'name': subcat['name']})

            return True, return_categories, ''

    @staticmethod
    def _get_default_providers():
        # name|url|key|cat_ids|enabled|search_mode|search_fallback|enable_daily|enable_backlog|enable_manualsearch
        return 'NZB.Cat|https://nzb.cat/||5030,5040,5010|0|eponly|0|0|0|0!!!' + \
               'NZBGeek|https://api.nzbgeek.info/||5030,5040|0|eponly|0|0|0|0!!!' + \
               'NZBs.org|https://nzbs.org/||5030,5040|0|eponly|0|0|0|0!!!' + \
               'Usenet-Crawler|https://www.usenet-crawler.com/||5030,5040|0|eponly|0|0|0|0!!!' + \
               'DOGnzb|https://api.dognzb.cr/||5030,5040,5060,5070|0|eponly|0|0|0|0!!!' + \
               'Omgwtfnzbs|https://api.omgwtfnzbs.me||5030,5040,5060,5070|0|eponly|0|0|0|0'
