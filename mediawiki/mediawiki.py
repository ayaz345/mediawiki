"""
MediaWiki class module
"""
# MIT License
# Author: Tyler Barrus (barrust@gmail.com)

import time
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from json import JSONDecodeError

import requests
import requests.exceptions as rex

from .exceptions import (
    HTTPTimeoutError,
    MediaWikiAPIURLError,
    MediaWikiCategoryTreeError,
    MediaWikiException,
    MediaWikiGeoCoordError,
    MediaWikiLoginError,
    PageError,
)
from .mediawikipage import MediaWikiPage
from .utilities import memoize

URL = "https://github.com/barrust/mediawiki"
VERSION = "0.7.3"


class MediaWiki(object):
    """ MediaWiki API Wrapper Instance

        Args:
            url (str): API URL of the MediaWiki site; defaults to Wikipedia
            lang (str): Language of the MediaWiki site; used to help change API URL
            timeout (float): HTTP timeout setting; None means no timeout
            rate_limit (bool): Use rate limiting to limit calls to the site
            rate_limit_wait (timedelta): Amount of time to wait between requests
            cat_prefix (str): The prefix for categories used by the mediawiki site; defaults to Category (en)
            user_agent (str): The user agent string to use when making requests; defaults to a library version but \
                per the MediaWiki API documentation it recommends setting a unique one and not using the \
                library's default user-agent string
            username (str): The username to use to log into the MediaWiki
            password (str): The password to use to log into the MediaWiki
            proxies (str): A dictionary of specific proxies to use in the Requests libary
            verify_ssl (bool|str): Verify SSL Certificates to be passed directly into the Requests library"""

    __slots__ = [
        "_version",
        "_lang",
        "_api_url",
        "_cat_prefix",
        "_timeout",
        "_user_agent",
        "_session",
        "_rate_limit",
        "_rate_limit_last_call",
        "_min_wait",
        "_extensions",
        "_api_version",
        "_api_version_str",
        "_base_url",
        "__supported_languages",
        "__available_languages",
        "_cache",
        "_refresh_interval",
        "_use_cache",
        "_is_logged_in",
        "_proxies",
        "_verify_ssl",
    ]

    def __init__(
        self,
        url="https://{lang}.wikipedia.org/w/api.php",
        lang="en",
        timeout=15.0,
        rate_limit=False,
        rate_limit_wait=timedelta(milliseconds=50),
        cat_prefix="Category",
        user_agent=None,
        username=None,
        password=None,
        proxies=None,
        verify_ssl=True,
    ):
        """ Init Function """
        self._version = VERSION
        self._lang = lang.lower()
        self._api_url = url.format(lang=self._lang)
        self._cat_prefix = None
        self.category_prefix = cat_prefix
        self._timeout = None
        self.timeout = timeout
        # requests library parameters
        self._session = None
        self._user_agent = ("python-mediawiki/VERSION-{0}" "/({1})/BOT").format(VERSION, URL)
        self._proxies = None
        self._verify_ssl = None
        self.verify_ssl = verify_ssl
        # set libary parameters
        if user_agent is not None:
            self.user_agent = user_agent
        self.proxies = proxies  # this will call self._reset_session()

        self._rate_limit = None
        self.rate_limit = bool(rate_limit)
        self._rate_limit_last_call = None
        self._min_wait = rate_limit_wait
        self._extensions = None
        self._api_version = None
        self._api_version_str = None
        self._base_url = None
        self.__supported_languages = None
        self.__available_languages = None

        # for memoized results
        self._cache = dict()
        self._refresh_interval = None
        self._use_cache = True

        # for login information
        self._is_logged_in = False
        if password is not None and username is not None:
            self.login(username, password)

        try:
            self._get_site_info()
        except MediaWikiException:
            raise MediaWikiAPIURLError(url)

    # non-settable properties
    @property
    def version(self):
        """ str: The version of the pymediawiki library

            Note:
                Not settable """
        return self._version

    @property
    def api_version(self):
        """ str: API Version of the MediaWiki site

            Note:
                Not settable """
        return self._api_version_str

    @property
    def base_url(self):
        """ str: Base URL for the MediaWiki site

            Note:
                Not settable """
        return self._base_url

    @property
    def extensions(self):
        """ list: Extensions installed on the MediaWiki site

            Note:
                Not settable """
        return self._extensions

    # settable properties
    @property
    def rate_limit(self):
        """ bool: Turn on or off Rate Limiting """
        return self._rate_limit

    @rate_limit.setter
    def rate_limit(self, rate_limit):
        """ Turn on or off rate limiting """
        self._rate_limit = bool(rate_limit)
        self._rate_limit_last_call = None
        self.clear_memoized()

    @property
    def proxies(self):
        """ dict: Turn on, off, or set proxy use with the Requests library """
        return self._proxies

    @proxies.setter
    def proxies(self, proxies):
        """ Turn on, off, or set proxy use through the Requests library """
        self._proxies = proxies if proxies and isinstance(proxies, dict) else None
        self._reset_session()

    @property
    def use_cache(self):
        """ bool: Whether caching should be used; on (**True**) or off \
                  (**False**)  """
        return self._use_cache

    @use_cache.setter
    def use_cache(self, use_cache):
        """ toggle using the cache or not """
        self._use_cache = bool(use_cache)

    @property
    def rate_limit_min_wait(self):
        """ timedelta: Time to wait between calls

            Note:
                 Only used if rate_limit is **True** """
        return self._min_wait

    @rate_limit_min_wait.setter
    def rate_limit_min_wait(self, min_wait):
        """ Set minimum wait to use for rate limiting """
        self._min_wait = min_wait
        self._rate_limit_last_call = None

    @property
    def timeout(self):
        """ float: Response timeout for API requests

            Note:
                Use **None** for no response timeout """
        return self._timeout

    @timeout.setter
    def timeout(self, timeout):
        """ Set request timeout in seconds (or fractions of a second) """

        if timeout is None:
            self._timeout = None  # no timeout
            return
        self._timeout = float(timeout)  # allow the exception to be raised

    @property
    def verify_ssl(self):
        """ bool | str: Verify SSL when using requests or path to cert file """
        return self._verify_ssl

    @verify_ssl.setter
    def verify_ssl(self, verify_ssl):
        """ Set request verify SSL parameter; defaults to True if issue """
        self._verify_ssl = verify_ssl if isinstance(verify_ssl, (bool, str)) else True
        self._reset_session()

    @property
    def language(self):
        """ str: The API URL language, if possible this will update the API \
                 URL

            Note:
                Use correct language titles with the updated API URL
            Note:
                Some API URLs do not encode language; unable to update if \
                this is the case """
        return self._lang

    @language.setter
    def language(self, lang):
        """ Set the language to use; attempts to change the API URL """
        lang = lang.lower()
        if self._lang == lang:
            return

        url = self._api_url
        tmp = url.replace("/{0}.".format(self._lang), "/{0}.".format(lang))

        self._api_url = tmp
        self._lang = lang
        self.clear_memoized()

    @property
    def category_prefix(self):
        """ str: The category prefix to use when using category based functions

            Note:
                Use the correct category name for the language selected """
        return self._cat_prefix

    @category_prefix.setter
    def category_prefix(self, prefix):
        """ Set the category prefix correctly """
        if prefix[-1:] == ":":
            prefix = prefix[:-1]
        self._cat_prefix = prefix

    @property
    def user_agent(self):
        """ str: User agent string

            Note: If using in as part of another project, this should be \
                  changed """
        return self._user_agent

    @user_agent.setter
    def user_agent(self, user_agent):
        """ Set the new user agent string

            Note: Will need to re-log into the MediaWiki if user agent string \
                 is changed """
        self._user_agent = user_agent
        self._reset_session()

    @property
    def api_url(self):
        """ str: API URL of the MediaWiki site

            Note:
                Not settable; See :py:func:`mediawiki.MediaWiki.set_api_url`"""
        return self._api_url

    @property
    def memoized(self):
        """ dict: Return the memoize cache

            Note:
                Not settable; see
                :py:func:`mediawiki.MediaWiki.clear_memoized` """
        return self._cache

    @property
    def refresh_interval(self):
        """ int: The interval at which the memoize cache is to be refresh """
        return self._refresh_interval

    @refresh_interval.setter
    def refresh_interval(self, refresh_interval):
        """ Set the new cache refresh interval """
        if isinstance(refresh_interval, int) and refresh_interval > 0:
            self._refresh_interval = refresh_interval
        else:
            self._refresh_interval = None

    def login(self, username, password, strict=True):
        """ Login as specified user

            Args:
                username (str): The username to log in with
                password (str): The password for the user
                strict (bool): `True` to throw an error on failure
            Returns:
                bool: `True` if successfully logged in; `False` otherwise
            Raises:
                :py:func:`mediawiki.exceptions.MediaWikiLoginError`: if unable to login

            Note:
                Per the MediaWiki API, one should use the `bot password`; \
                see https://www.mediawiki.org/wiki/API:Login for more information """
        # get login token
        params = {
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json",
        }
        token_res = self._get_response(params)
        if "query" in token_res and "tokens" in token_res["query"]:
            token = token_res["query"]["tokens"]["logintoken"]

        params = {
            "action": "login",
            "lgname": username,
            "lgpassword": password,
            "lgtoken": token,
            "format": "json",
        }

        res = self._post_response(params)
        if res["login"]["result"] == "Success":
            self._is_logged_in = True
            return True
        self._is_logged_in = False
        if strict:
            reason = res["login"]["reason"]
            msg = f"MediaWiki login failure: {reason}"
            raise MediaWikiLoginError(msg)
        return False

    # non-properties
    def set_api_url(
        self, api_url="https://{lang}.wikipedia.org/w/api.php", lang="en", username=None, password=None,
    ):
        """ Set the API URL and language

            Args:
                api_url (str): API URL to use
                lang (str): Language of the API URL
                username (str): The username, if needed, to log into the MediaWiki site
                password (str): The password, if needed, to log into the MediaWiki site
            Raises:
                :py:func:`mediawiki.exceptions.MediaWikiAPIURLError`: if the \
                url is not a valid MediaWiki site or login fails """
        old_api_url = self._api_url
        old_lang = self._lang
        self._lang = lang.lower()
        self._api_url = api_url.format(lang=self._lang)

        self._is_logged_in = False
        try:
            if username is not None and password is not None:
                self.login(username, password)
            self._get_site_info()
            self.__supported_languages = None  # reset this
            self.__available_languages = None  # reset this
        except (rex.ConnectTimeout, MediaWikiException):
            # reset api url and lang in the event that the exception was caught
            self._api_url = old_api_url
            self._lang = old_lang
            raise MediaWikiAPIURLError(api_url)
        self.clear_memoized()

    def _reset_session(self):
        """ Set session information """
        if self._session:
            self._session.close()

        headers = {"User-Agent": self._user_agent}
        self._session = requests.Session()
        self._session.headers.update(headers)
        if self._proxies is not None:
            self._session.proxies.update(self._proxies)
        self._session.verify = self._verify_ssl
        self._is_logged_in = False

    def clear_memoized(self):
        """ Clear memoized (cached) values """
        if hasattr(self, "_cache"):
            self._cache.clear()

    # non-setup functions
    @property
    def supported_languages(self):
        """ dict: All supported language prefixes on the MediaWiki site

            Note:
                Not Settable """
        if self.__supported_languages is None:
            res = self.wiki_request({"meta": "siteinfo", "siprop": "languages"})
            tmp = res["query"]["languages"]
            supported = {lang["code"]: lang["*"] for lang in tmp}
            self.__supported_languages = supported
        return self.__supported_languages

    @property
    def available_languages(self):
        """ dict: All available language prefixes on the MediaWiki site

            Note:
                Not Settable """
        if self.__available_languages is None:
            available = {}
            for lang in self.supported_languages:
                try:
                    MediaWiki(lang=lang)
                    available[lang] = True
                except (rex.ConnectionError, rex.ConnectTimeout, MediaWikiException, MediaWikiAPIURLError):
                    available[lang] = False
            self.__available_languages = available
        return self.__available_languages

    @property
    def logged_in(self):
        """ bool: Returns if logged into the MediaWiki site """
        return self._is_logged_in

    def random(self, pages=1):
        """ Request a random page title or list of random titles

            Args:
                pages (int): Number of random pages to return
            Returns:
                list or int: A list of random page titles or a random page title if pages = 1 """
        if pages is None or pages < 1:
            raise ValueError("Number of pages must be greater than 0")

        query_params = {"list": "random", "rnnamespace": 0, "rnlimit": pages}

        request = self.wiki_request(query_params)
        titles = [page["title"] for page in request["query"]["random"]]

        return titles[0] if len(titles) == 1 else titles

    @memoize
    def allpages(self, query="", results=10):
        """ Request all pages from mediawiki instance

            Args:
                query (str): Search string to use for pulling pages
                results (int): The number of pages to return
            Returns:
                list: The pages that meet the search query
            Note:
                Could add ability to continue past the limit of 500
        """
        max_pull = 500
        limit = min(results, max_pull) if results is not None else max_pull
        query_params = {"list": "allpages", "aplimit": limit, "apfrom": query}

        request = self.wiki_request(query_params)

        self._check_error_response(request, query)

        return [page["title"] for page in request["query"]["allpages"]]

    @memoize
    def search(self, query, results=10, suggestion=False):
        """ Search for similar titles

            Args:
                query (str): Page title
                results (int): Number of pages to return
                suggestion (bool): Use suggestion
            Returns:
                tuple or list: tuple (list results, suggestion) if suggestion is **True**; list of results otherwise
            Note:
                Could add ability to continue past the limit of 500
        """

        self._check_query(query, "Query must be specified")

        max_pull = 500

        search_params = {
            "list": "search",
            "srprop": "",
            "srlimit": min(results, max_pull) if results is not None else max_pull,
            "srsearch": query,
            "sroffset": 0,  # this is what will be used to pull more than the max
        }
        if suggestion:
            search_params["srinfo"] = "suggestion"

        raw_results = self.wiki_request(search_params)

        self._check_error_response(raw_results, query)

        search_results = [d["title"] for d in raw_results["query"]["search"]]

        if suggestion:
            sug = None
            if raw_results["query"].get("searchinfo"):
                sug = raw_results["query"]["searchinfo"]["suggestion"]
            return search_results, sug
        return search_results

    @memoize
    def suggest(self, query):
        """ Gather suggestions based on the provided title or None if no
            suggestions found

            Args:
                query (str): Page title
            Returns:
                String or None: Suggested page title or **None** if no suggestion found
        """
        res, suggest = self.search(query, results=1, suggestion=True)
        try:
            title = res[0] or suggest
        except IndexError:  # page doesn't exist
            title = None
        return title

    @memoize
    def geosearch(
        self, latitude=None, longitude=None, radius=1000, title=None, auto_suggest=True, results=10,
    ):
        """ Search for pages that relate to the provided geocoords or near
            the page

            Args:
                latitude (Decimal or None): Latitude geocoord; must be coercible to decimal
                longitude (Decimal or None): Longitude geocoord; must be coercible to decimal
                radius (int): Radius around page or geocoords to pull back; in meters
                title (str): Page title to use as a geocoordinate; this has precedence over lat/long
                auto_suggest (bool): Auto-suggest the page title
                results (int): Number of pages within the radius to return
            Returns:
                list: A listing of page titles
            Note:
                The Geosearch API does not support pulling more than the maximum of 500
            Note:
                If the page doesn't match the provided title, try setting auto_suggest to `False`
            Raises:
                ValueError: If either the passed latitude or longitude are not coercible to a Decimal
        """

        def test_lat_long(val):
            """ handle testing lat and long """
            if not isinstance(val, Decimal):
                error = (
                    "Latitude and Longitude must be specified either as "
                    "a Decimal or in formats that can be coerced into "
                    "a Decimal."
                )
                try:
                    return Decimal(val)
                except (DecimalException, TypeError):
                    raise ValueError(error)
            return val

        # end local function
        max_pull = 500

        limit = min(results, max_pull) if results is not None else max_pull
        params = {"list": "geosearch", "gsradius": radius, "gslimit": limit}
        if title is not None:
            if auto_suggest:
                title = self.suggest(title)
            params["gspage"] = title
        else:
            lat = test_lat_long(latitude)
            lon = test_lat_long(longitude)
            params["gscoord"] = "{0}|{1}".format(lat, lon)

        raw_results = self.wiki_request(params)

        self._check_error_response(raw_results, title)

        return [d["title"] for d in raw_results["query"]["geosearch"]]

    @memoize
    def opensearch(self, query, results=10, redirect=True):
        """ Execute a MediaWiki opensearch request, similar to search box
            suggestions and conforming to the OpenSearch specification

            Args:
                query (str): Title to search for
                results (int): Number of pages within the radius to return
                redirect (bool): If **False** return the redirect itself, otherwise resolve redirects
            Returns:
                List: List of results that are stored in a tuple (Title, Summary, URL)
            Note:
                The Opensearch API does not support pulling more than the maximum of 500
            Raises:
        """

        self._check_query(query, "Query must be specified")
        max_pull = 500

        query_params = {
            "action": "opensearch",
            "search": query,
            "limit": (min(results, max_pull) if results is not None else max_pull),
            "redirects": ("resolve" if redirect else "return"),
            "warningsaserror": True,
            "namespace": "",
        }

        results = self.wiki_request(query_params)

        self._check_error_response(results, query)

        return [
            (item, results[2][i], results[3][i])
            for i, item in enumerate(results[1])
        ]

    @memoize
    def prefixsearch(self, prefix, results=10):
        """ Perform a prefix search using the provided prefix string

            Args:
                prefix (str): Prefix string to use for search
                results (int): Number of pages with the prefix to return
            Returns:
                list: List of page titles
            Note:
                **Per the documentation:** "The purpose of this module is \
                similar to action=opensearch: to take user input and provide \
                the best-matching titles. Depending on the search engine \
                backend, this might include typo correction, redirect \
                avoidance, or other heuristics."
            Note:
                Could add ability to continue past the limit of 500
        """

        self._check_query(prefix, "Prefix must be specified")

        query_params = {
            "list": "prefixsearch",
            "pssearch": prefix,
            "pslimit": ("max" if (results > 500 or results is None) else results),
            "psnamespace": 0,
            "psoffset": 0,  # parameterize to skip to later in the list?
        }

        raw_results = self.wiki_request(query_params)

        self._check_error_response(raw_results, prefix)

        return [rec["title"] for rec in raw_results["query"]["prefixsearch"]]

    @memoize
    def summary(self, title, sentences=0, chars=0, auto_suggest=True, redirect=True):
        """ Get the summary for the title in question

            Args:
                title (str): Page title to summarize
                sentences (int): Number of sentences to return in summary
                chars (int): Number of characters to return in summary
                auto_suggest (bool): Run auto-suggest on title before summarizing
                redirect (bool): Use page redirect on title before summarizing
            Returns:
                str: The summarized results of the page
            Note:
                Precedence for parameters: sentences then chars; if both are \
                0 then the entire first section is returned
            Note:
                If the page doesn't match the provided title, try setting auto_suggest to `False`"""
        page_info = self.page(title, auto_suggest=auto_suggest, redirect=redirect)
        return page_info.summarize(sentences, chars)

    @memoize
    def categorymembers(self, category, results=10, subcategories=True):
        """ Get information about a category: pages and subcategories

            Args:
                category (str): Category name
                results (int): Number of result
                subcategories (bool): Include subcategories (**True**) or not (**False**)
            Returns:
                Tuple or List: Either a tuple ([pages], [subcategories]) or just the list of pages
            Note:
                Set results to **None** to get all results """
        self._check_query(category, "Category must be specified")

        max_pull = 500
        search_params = {
            "list": "categorymembers",
            "cmprop": "ids|title|type",
            "cmtype": ("page|subcat|file" if subcategories else "page|file"),
            "cmlimit": (min(results, max_pull) if results is not None else max_pull),
            "cmtitle": "{0}:{1}".format(self.category_prefix, category),
        }
        pages = []
        subcats = []
        returned_results = 0
        finished = False
        last_cont = dict()
        while not finished:
            params = search_params.copy()
            params.update(last_cont)
            raw_res = self.wiki_request(params)

            self._check_error_response(raw_res, category)

            current_pull = len(raw_res["query"]["categorymembers"])
            for rec in raw_res["query"]["categorymembers"]:
                if rec["type"] in ("page", "file"):
                    pages.append(rec["title"])
                elif rec["type"] == "subcat":
                    tmp = rec["title"]
                    if tmp.startswith(self.category_prefix):
                        tmp = tmp[len(self.category_prefix) + 1 :]
                    subcats.append(tmp)

            cont = raw_res.get("query-continue", False)
            if cont and "categorymembers" in cont:
                cont = cont["categorymembers"]
            else:
                cont = raw_res.get("continue", False)

            if cont is False or last_cont == cont:
                break

            returned_results += current_pull
            if results is None or (results - returned_results > 0):
                last_cont = cont
            else:
                finished = True

            if results is not None and results - returned_results < max_pull:
                search_params["cmlimit"] = results - returned_results
        # end while loop

        return (pages, subcats) if subcategories else pages

    def categorytree(self, category, depth=5):
        """ Generate the Category Tree for the given categories

            Args:
                category(str or list of strings): Category name(s)
                depth(int): Depth to traverse the tree
            Returns:
                dict: Category tree structure
            Note:
                Set depth to **None** to get the whole tree
            Note:
                Return Data Structure: Subcategory contains the same recursive structure

                >>> {
                        'category': {
                            'depth': Number,
                            'links': list,
                            'parent-categories': list,
                            'sub-categories': dict
                        }
                    }

            .. versionadded:: 0.3.10 """

        # make it simple to use both a list or a single category term
        cats = [category] if not isinstance(category, list) else category

        self.__category_parameter_verification(cats, depth, category)

        results = dict()
        categories = dict()
        links = dict()

        for cat in [x for x in cats if x]:
            self.__cat_tree_rec(cat, depth, results, 0, categories, links)
        return results

    def page(self, title=None, pageid=None, auto_suggest=True, redirect=True, preload=False):
        """ Get MediaWiki page based on the provided title or pageid

            Args:
                title (str): Page title
                pageid (int): MediaWiki page identifier
                auto-suggest (bool): **True:** Allow page title auto-suggest
                redirect (bool): **True:** Follow page redirects
                preload (bool): **True:** Load most page properties
            Raises:
                ValueError: when title is blank or None and no pageid is provided
            Raises:
                :py:func:`mediawiki.exceptions.PageError`: if page does not exist
            Note:
                Title takes precedence over pageid if both are provided
            Note:
                If the page doesn't match the provided title, try setting auto_suggest to `False`"""
        if (title is None or title.strip() == "") and pageid is None:
            raise ValueError("Either a title or a pageid must be specified")
        if title:
            if auto_suggest:
                temp_title = self.suggest(title)
                if temp_title is None:  # page doesn't exist
                    raise PageError(title=title)
                title = temp_title
            return MediaWikiPage(self, title, redirect=redirect, preload=preload)
        return MediaWikiPage(self, pageid=pageid, preload=preload)

    def wiki_request(self, params):
        """ Make a request to the MediaWiki API using the given search
            parameters

            Args:
                params (dict): Request parameters
            Returns:
                A parsed dict of the JSON response
            Note:
                Useful when wanting to query the MediaWiki site for some \
                value that is not part of the wrapper API """

        params["format"] = "json"
        if "action" not in params:
            params["action"] = "query"

        limit = self._rate_limit
        last_call = self._rate_limit_last_call
        if limit and last_call and last_call + self._min_wait > datetime.now():
            # call time to quick for rate limited api requests, wait
            wait_time = (last_call + self._min_wait) - datetime.now()
            time.sleep(wait_time.total_seconds())

        req = self._get_response(params)

        if self._rate_limit:
            self._rate_limit_last_call = datetime.now()

        return req

    # Protected functions
    def _get_site_info(self):
        """ Parse out the Wikimedia site information including API Version and Extensions """
        response = self.wiki_request({"meta": "siteinfo", "siprop": "extensions|general"})

        # parse what we need out here!
        query = response.get("query", None)
        if query is None or query.get("general", None) is None:
            raise MediaWikiException("Missing query in response")

        gen = query.get("general", None)

        api_version = gen["generator"].split(" ")[1].split("-")[0]

        major_minor = api_version.split(".")
        for i, item in enumerate(major_minor):
            major_minor[i] = int(item)
        self._api_version = tuple(major_minor)
        self._api_version_str = ".".join([str(x) for x in self._api_version])

        # parse the base url out
        tmp = gen.get("server", "")
        if tmp == "":
            raise MediaWikiException("Unable to parse base url")
        if tmp.startswith("http://") or tmp.startswith("https://"):
            self._base_url = tmp
        elif gen["base"].startswith("https:"):
            self._base_url = f"https:{tmp}"
        else:
            self._base_url = f"http:{tmp}"

        self._extensions = [ext["name"] for ext in query["extensions"]]
        self._extensions = sorted(list(set(self._extensions)))

    # end _get_site_info

    @staticmethod
    def _check_error_response(response, query):
        """ check for default error messages and throw correct exception """
        if "error" in response:
            http_error = ["HTTP request timed out.", "Pool queue is full"]
            geo_error = [
                "Page coordinates unknown.",
                "One of the parameters gscoord, gspage, gsbbox is required",
                "Invalid coordinate provided",
            ]
            err = response["error"]["info"]
            if err in http_error:
                raise HTTPTimeoutError(query)
            if err in geo_error:
                raise MediaWikiGeoCoordError(err)
            raise MediaWikiException(err)

    @staticmethod
    def _check_query(value, message):
        """ check if the query is 'valid' """
        if value is None or value.strip() == "":
            raise ValueError(message)

    @staticmethod
    def __category_parameter_verification(cats, depth, category):
        # parameter verification
        if len(cats) == 1 and (cats[0] is None or cats[0] == ""):
            msg = f"CategoryTree: Parameter 'category' must either be a list of one or more categories or a string; provided: '{category}'"
            raise ValueError(msg)

        if depth is not None and depth < 1:
            msg = "CategoryTree: Parameter 'depth' must be either None " "(for the full tree) or be greater than 0"
            raise ValueError(msg)

    def __cat_tree_rec(self, cat, depth, tree, level, categories, links):
        """ recursive function to build out the tree """
        tree[cat] = dict()
        tree[cat]["depth"] = level
        tree[cat]["sub-categories"] = dict()
        tree[cat]["links"] = []
        tree[cat]["parent-categories"] = []
        parent_cats = []

        if cat not in categories:
            tries = 0
            while True:
                if tries > 10:
                    raise MediaWikiCategoryTreeError(cat)
                try:
                    pag = self.page("{0}:{1}".format(self.category_prefix, cat))
                    categories[cat] = pag
                    parent_cats = categories[cat].categories
                    links[cat] = self.categorymembers(cat, results=None, subcategories=True)
                    break
                except PageError:
                    raise PageError("{0}:{1}".format(self.category_prefix, cat))
                except KeyboardInterrupt:
                    raise
                except Exception:
                    tries = tries + 1
                    time.sleep(1)
        else:
            parent_cats = categories[cat].categories

        tree[cat]["parent-categories"].extend(parent_cats)
        tree[cat]["links"].extend(links[cat][0])

        if depth and level >= depth:
            for ctg in links[cat][1]:
                tree[cat]["sub-categories"][ctg] = None
        else:
            for ctg in links[cat][1]:
                self.__cat_tree_rec(
                    ctg, depth, tree[cat]["sub-categories"], level + 1, categories, links,
                )

    def _get_response(self, params):
        """ wrap the call to the requests package """
        try:
            return self._session.get(self._api_url, params=params, timeout=self._timeout).json()
        except JSONDecodeError:
            return {}

    def _post_response(self, params):
        """ wrap a post call to the requests package """
        try:
            return self._session.post(self._api_url, data=params, timeout=self._timeout).json()
        except JSONDecodeError:
            return {}


# end MediaWiki class
