from pprint import pprint
import feedparser
from readability.readability import Document
import requests
import codecs
import logging
from time import mktime
from datetime import datetime, timedelta
from feedgen.feed import FeedGenerator
from expiringdict import ExpiringDict
from base64 import b64encode
import logging
import time
from logging.config import dictConfig
from logging.handlers import TimedRotatingFileHandler
from sendgrid import sendgrid
import os
from urlparse import urlparse
from flask import Flask, request, send_file
import socket
start_datetime = None
send_email = None


def gen_stats():
    global stats
    s = ""
    for k, v in stats.items():
        ts = [t["time"] for t in v["requests"]]
        t0 = ts[0]
        delta_a = []
        for e in range(len(ts)-1):
            delta = ts[e+1] - t0
            delta_a.append(delta)
            t0 = ts[e+1]
        avg_delta = "infinity"
        if len(delta_a)!=0:
            avg_delta = sum(delta_a, timedelta())/len(delta_a)
        s += "For %s, %s requests, avg processing time = %s, delta between requests = %s\r\n\r\n"%(k, len(v["requests"]), sum(v["processing_time"], timedelta())/len(v["processing_time"]), avg_delta)
        s += "\r\n".join(["%s - %s"%(e['time'], e['ip']) for e in v["requests"]])
    return s


def send_stats():
    global stats
    s = gen_stats()
    send_email("Rss proxy stats", s)
    stats = {}
    
def error_report():
    with open('logs/critical.log') as f:
        t = f.read()
        if len(t)>0:
            send_email("Rss proxy errors", t)
        else:
            logger.info("No error in last period")
        for handler in logger.handlers:
            try:
                handler.doRollover()
            except:
                pass

def send_reporting_if_needed():
    global last_error_reporting
    global last_stats_reporting
    stats_interval = os.environ.get('STATS_INTERVAL_SEC')
    error_interval = os.environ.get('ERROR_REPORT_INTERVAL_SEC')
    if not stats_interval:
        stats_interval = 3600 * 24
    if not error_interval:
        error_interval = 3600 * 24
    if (datetime.now() - last_stats_reporting) > timedelta(seconds=stats_interval):
        logger.debug("Stats reporting")
        #Report stat
        gen_stats()
        last_stats_reporting = datetime.now()
    if (datetime.now() - last_error_reporting) > timedelta(seconds=error_interval):
        #Report error
        error_report()
        logger.debug("Error reporting")
        last_error_reporting = datetime.now()


debug_path = os.environ.get('DEBUG_FILE_PATH')
if not debug_path:
    debug_path = 'logs/debug.log'
logging_config = dict(
    version = 1,
    formatters = {
        'f': {'format':
              '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'}
        },
    handlers = {
        'console': {'class': 'logging.StreamHandler',
              'formatter': 'f',
              'level': logging.INFO},
        'filedebug': {'class': 'logging.handlers.TimedRotatingFileHandler',
              'filename': debug_path,
              'when': 'midnight',
              'interval': 1,
              'backupCount': 7,
              'formatter': 'f',
              'level': logging.DEBUG},
        'filecritical': {'class': 'logging.handlers.TimedRotatingFileHandler',
              'filename': 'logs/critical.log',
              'when': 'midnight',
              # 'when': 'M',
              'interval': 1,
              'backupCount': 7,
              'formatter': 'f',
              'level': logging.ERROR},
        },
    root = {
        'handlers': ['console', 'filedebug', 'filecritical'],
        'level': logging.DEBUG,
        },
)

dictConfig(logging_config)

logger = logging.getLogger()

def send_email(subject="test", body="test"):
    apikey = os.environ.get('SENDGRID_API_KEY')
    emailto = os.environ.get('EMAIL_TO')
    if not apikey or not emailto:
        logger.warning("No email will be send (SENDGRID_API_KEY, EMAIL_TO env variable not defined)")
        return False
    sg = sendgrid.SendGridAPIClient(apikey=apikey)
    emailfrom = "root@%s"%socket.getfqdn()
    data = {
      "personalizations": [
        {
          "to": [
            {
              "email": emailto
            }
          ],
          "subject": "%s"%subject
        }
      ],
      "from": {
        "email": emailfrom
      },
      "content": [
        {
          "type": "text/plain",
          "value": "%s"%body
        }
      ]
    }
    try:
        response = sg.client.mail.send.post(request_body=data)
        logger.debug(response.status_code)
        logger.debug(response.body)
        logger.debug(response.headers)
    except Exception as e:
        logger.error("Got exception %s"%e)
        logger.exception("Got exception %s"%e)


store = ExpiringDict(max_len=5000, max_age_seconds=3600*48)


# Create application
app = Flask(__name__)

@app.route('/stats')
def stats():
    return gen_stats()

@app.route('/debug')
def debug():
    return send_file(debug_path, attachment_filename='debug.log')

@app.route('/critical')
def critical():
    return send_file('logs/critical.log', attachment_filename='critical.log')

@app.route('/uptime')
def uptime():
    global start_datetime
    if not start_datetime:
        return "1st request"
    return "Start time : %s    \r\nUptime : %s"%(start_datetime, datetime.now()-start_datetime)
@app.route('/')
def index():
    url = request.args.get('url', None)
    send_reporting_if_needed()
    if not url:
        return ""
    global stats
    new_feed = False
    if not url in stats:
        new_feed = True
        stats[url] = {'processing_time': [], 'requests': [], 'last_cache_hits': 0}
    logger.info("Request from %s for %s"%(request.remote_addr, url))
    stats[url]['requests'].append({'ip': request.remote_addr, 'time': datetime.now()})
    tstart = datetime.now()
    try:
        i, r = Rss(url).fetch()
    except Exception as e:
        r = "Got a fatal exception while processing %s : %s"%(url, e)
        logger.error(r)
        logger.exception(r)
        return r
    tend = datetime.now()
    stats[url]['processing_time'].append(tend-tstart)
    
    logger.info("Request from %s for %s took %s to complete (%s entries loaded)"%(request.remote_addr, url, tend-tstart, i))
    if not new_feed and stats[url]['last_cache_hits'] == 0:
        logger.error("No cache hit for %s (occurs on first request for a feed or if feeds are not pulled fast enough)"%url)
    return r

class Rss():
    blacklisted_domains = ExpiringDict(max_len=5000, max_age_seconds=3600*24)
    def __init__(self, url):
        self.url = url

    def fetch(self):
        global stats
        url = self.url
        stats[url]['last_cache_hits']=0
        url = self.url
        self.fg = FeedGenerator()
        logger.debug("Starting fetch for %s"%(url))
        d = feedparser.parse(url)
        self.fg.title(d.feed.title)
        # self.fg.updated(d.feed.updated)
        self.fg.description(d.feed.description)
        self.fg.link(d.feed.links)
        logger.debug("Got %s entries in the feed"%(len(d.entries)))
        i = -1
        for i, e in enumerate(d.entries):
            try:
                self.fetch_article(e)
            except Exception as ex:
                logger.error("Got exception while fetching %s : %s"%(e, ex))
                logger.exception("")
        return (i+1, self.fg.rss_str())
        
    def fetch_article(self, e):
        url = e['link']
        link = url
        title = e['title']
        description = e['description']
        article_id = e['id']
        try:
            published = e['updated_parsed']
        except KeyError as e:
            logger.debug("No parsable date in feed item, gonna use cuurent time")
            published = datetime.now()
        try:
            fetched_content = self.__fetchFullArticle(url)
        except Exception as e:
            fetched_content = "Got an exception while fetching %s : %s"%(url, e)
            logger.error(fetched_content)
            logger.exception(fetched_content)
        # (title=title,link=link,description=description,article_id=article_id,published=published,fetched_content=fetched_content,feed=self.feed,fetched_time=datetime.now()    )
        fe = self.fg.add_entry()
        fe.id(article_id)
        fe.title(title)
        fe.link(href=link)
        fe.description(description)
        fe.content(fetched_content)

    def __fetchFullArticle(self,url):
        global stats
        key = b64encode(url)
        data = store.get(key)
        if data:
            logger.debug("Cache hit for %s"%url)
            stats[self.url]['last_cache_hits']+=1
            return data
        parsed_uri = urlparse(url)
        domain = parsed_uri.netloc
        if self.blacklisted_domains.get(domain):
            msg = "Skipped blacklisted non responsive domain : %s"%domain
            logger.warning(msg)
            return msg
        logger.debug("fetching %s"%(url))
        tstart = datetime.now()
        try:
            html = requests.get(url, verify=False, timeout=5)
        except requests.exceptions.Timeout as e:
            self.blacklisted_domains[domain] = True
            logging.warning("Blacklisting non responsive domain : %s, %s"%(domain, e))
            return str(e)
        readable_article = Document(html.text).summary()
        logger.debug("It took %s to fetch %s"%(datetime.now()-tstart, url))
        # readable_title = Document(html).short_title()
        store[key] = readable_article
        return readable_article

@app.before_first_request
def before_first_request():
    logger.info("Application restarted")
    send_email("RSS app start", "Starting application...")
    global start_datetime
    start_datetime = datetime.now()
        
        
        
if __name__ == "__main__":
    stats = {}
    last_stats_reporting = datetime.now()
    last_error_reporting = datetime.now()
    if os.environ.get('THREADED'):
        threaded = True
    else:
        threaded = False
    if os.environ.get('DEBUG'):
        debug = True
    else:
        debug = False
    app.run(debug=debug, host='0.0.0.0', port=8080, threaded=threaded)

