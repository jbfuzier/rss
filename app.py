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

store = ExpiringDict(max_len=20000, max_age_seconds=3600*48)

from flask import Flask, request
# Create application
app = Flask(__name__)


logger = logging.getLogger()
logger.setLevel(logging.DEBUG)


@app.route('/')
def index():
    url = request.args.get('url', '')
    return Rss(url).fetch()
     



class Rss():
    def __init__(self, url):
        self.fg = FeedGenerator()
        logger.debug("Starting fetch for %s"%(url))
        d = feedparser.parse(url)
        self.fg.title(d.feed.title)
        self.fg.updated(d.feed.updated)
        self.fg.description(d.feed.description)
        self.fg.link(d.feed.links)
        logger.debug("Got %s entries in the feed"%(len(d.entries)))
        for e in d.entries:
            self.fetch_article(e)

    def fetch(self):
        return self.fg.rss_str(pretty=True)
        
    def fetch_article(self, e):
        url = e['link']
        link = url
        title = e['title']
        description = e['description']
        article_id = e['id']
        published = datetime.now()
        fetched_content = self.__fetchFullArticle(url)
        # (title=title,link=link,description=description,article_id=article_id,published=published,fetched_content=fetched_content,feed=self.feed,fetched_time=datetime.now()    )
        fe = self.fg.add_entry()
        fe.id(article_id)
        fe.title(title)
        print type(link)
        fe.link(href=link)
        fe.description(description)
        fe.content(fetched_content)

    def __fetchFullArticle(self,url):
        key = b64encode(url)
        data = store.get(key)
        if data:
            print "Cache hit for %s"%url
            return data
        logger.debug("fetching %s"%(url))
        html = requests.get(url)
        readable_article = Document(html.text).summary()
        # readable_title = Document(html).short_title()
        store[key] = readable_article
        return readable_article
        





if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    # a = Rss('http://www.generation-nt.com/export/rss.xml')
    # a = Rss('www.pcinpact.com/include/news.xml')
    # Start app
    app.run(debug=True, host='0.0.0.0', port=8080, threaded=True)

