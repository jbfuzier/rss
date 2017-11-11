from pprint import pprint
import feedparser
from readability.readability import Document
import requests
import codecs
import logging
from time import mktime
from datetime import datetime
from feedgen.feed import FeedGenerator
from expiringdict import ExpiringDict
from base64 import b64encode
import logging
from logging.config import dictConfig
import os


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
              'filename': 'logs/debug.log',
              'when': 'midnight',
              'backupCount': 7,
              'formatter': 'f',
              'level': logging.DEBUG},
        'filecritical': {'class': 'logging.handlers.TimedRotatingFileHandler',
              'filename': 'logs/critical.log',
              'when': 'midnight',
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

store = ExpiringDict(max_len=2000, max_age_seconds=3600*48)

from flask import Flask, request, send_file
# Create application
app = Flask(__name__)


@app.route('/debug')
def debug():
    return send_file('logs/debug.log', attachment_filename='debug.log')

@app.route('/critical')
def critical():
    return send_file('logs/critical.log', attachment_filename='critical.log')

@app.route('/')
def index():
    url = request.args.get('url', None)
    if not url:
        return ""
    logger.info("Request from %s for %s"%(request.remote_addr, url))
    tstart = datetime.now()
    try:
        i, r = Rss(url).fetch()
    except Exception as e:
        r = "Got a fatal exception while processing %s : %s"%(url, e)
        logger.error(r)
        logger.exception(r)
        return r
    tend = datetime.now()
    logger.info("Request from %s for %s took %s to complete (%s entries loaded)"%(request.remote_addr, url, tend-tstart, i))
    return r

class Rss():
    def __init__(self, url):
        self.url = url

    def fetch(self):
        url = self.url
        self.fg = FeedGenerator()
        logger.debug("Starting fetch for %s"%(url))
        d = feedparser.parse(url)
        self.fg.title(d.feed.title)
        self.fg.updated(d.feed.updated)
        self.fg.description(d.feed.description)
        self.fg.link(d.feed.links)
        logger.debug("Got %s entries in the feed"%(len(d.entries)))
        for i, e in enumerate(d.entries):
            try:
                self.fetch_article(e)
            except Exception as ex:
                logger.error("Got exception while fetching %s : %s"%(e, ex))
                logger.exception()
        return (i+1, self.fg.rss_str())
        
    def fetch_article(self, e):
        url = e['link']
        link = url
        title = e['title']
        description = e['description']
        article_id = e['id']
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
        key = b64encode(url)
        data = store.get(key)
        if data:
            logger.debug("Cache hit for %s"%url)
            return data
        logger.debug("fetching %s"%(url))
        tstart = datetime.now()
        html = requests.get(url)
        readable_article = Document(html.text).summary()
        logger.debug("It took %s to fetch %s"%(datetime.now()-tstart, url))
        # readable_title = Document(html).short_title()
        store[key] = readable_article
        return readable_article


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8080, threaded=True)

