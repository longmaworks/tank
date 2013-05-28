#!/usr/bin/env python
# -*- coding:utf-8 -*-

import json
import urllib
import datetime, time
import traceback
import base64
import md5

from tornado.web import RequestHandler
from tornado.web import Application
from tornado.web import HTTPError
from tornado import ioloop

import tornado.escape

import tornadoext.oauth2

from tank import const
from tank import config
from tank import dtutils

from tank.models import utils as models_uuids

from tank.models import *
#from tame.services import *

VERSION_1_SALT = 'a5cd9026'

def encode_user_token(uid, params = {}, version=1):
    uid = str(uid)
    params['uid']  = uid
    params['created_at'] = int(time.time())

    param_fragments = []
    for param in sorted(params.iteritems(), key=lambda x: x[0]):
        param_fragments.append('%s=%s' % (param[0], param[1]))

    s = '&'.join(param_fragments)
    if version != 1:
        return None
    elif version == 1:
        #only base64
        s = base64.b64encode(s).replace('+','-').replace('/','_').replace('=','')
        verify_code = md5.md5(uid + VERSION_1_SALT + s).hexdigest()[:8]
        
        s = "%s.%s.%s" % (version, s, verify_code)
        return s
        
def decode_user_token(user_token):
    parts = user_token.split('.')
    #print parts
    if not len(parts) == 3:
        return 'wrong token', None

    if parts[0] != '1':
        return 'wrong version', None

    d = parts[1].replace('-', '+').replace('_','/')
    #print len(d), len(d) % 4
    if len(d) % 4 != 0:
        d = d + '=' * (4 - len(d) % 4)
    #print d
    d = base64.b64decode(d)
    #print d

    salt = ''
    if parts[0] == '1':
        params = {}
        for pair in d.split('&'):
            pair = pair.split('=')
            params[pair[0]] = pair[1]
        salt = VERSION_1_SALT

    if not 'uid' in params:
        return 'no uid', None

    verify_code = md5.md5(params['uid'] + salt + parts[1]).hexdigest()[:8]
    if verify_code != parts[2]:
        return 'verify code don`t match', None

    return None, params

def need_login(func):
    def wrapper(*args, **kwargs):
        handler = None
        if len(args) > 0:
            handler = args[0]

        if handler and isinstance(handler, RequestHandler):
            user = handler.get_current_user()
            if not user:
                uri = handler.request.uri
                return handler.redirect("/login?back_url=" + uri)

        return func(*args, **kwargs)

    return wrapper


class BaseHandler(RequestHandler):

    def initialize(self, app_config, Session):
        self.app_config = app_config
        self.Session = Session
        self.db_session = None

    def get_current_user(self):
        user_token = self.get_cookie('user_token')
        if not user_token:
            return None

        ret, params = decode_user_token(user_token)
        if ret:
            print params
            return None
        else:
            return params

    def get_db_session(self):
        if not self.db_session:
            self.db_session = self.Session()
            #self.db_session.begin()

        """
        #Remember to call session.remove() after using
        return self.Session()
        """
        return self.db_session


    def _emmed_css(self, *args):
        css_path = "%s/src/static/css/" % (self.app_config['running_home'])
        content = ""

        for i in range(len(args)):
            css_file = "%s%s" % (css_path, args[i])

            handler = open(css_file, "r")
            content += handler.read()
            handler.close()

        return content

    def _emmed_js(self, *args):
        js_path = "%s/src/static/js/" % (self.app_config['running_home'])
        content = ""

        for i in range(len(args)):
            js_file = "%s%s" % (js_path, args[i])

            handler = open(js_file, "r")
            content += handler.read()
            handler.close()

        return content
    
    def _ui_list(self, html, data):
        temp = html.replace("%%", "");
        param_num = len(temp) - len(temp.replace("%", ""))
        content = ''

        for d in data:
            if len([None for i in range(param_num) if d[i] is None]) > 0:
                continue

            content += html % d

        return content
        
    def get_template_namespace(self):
        namespace = RequestHandler.get_template_namespace(self)
        namespace["emmed_css"] = self._emmed_css
        namespace["emmed_js"] = self._emmed_js
        namespace["ui_list"] = self._ui_list
        
        return namespace

    def on_finish(self):
        if self.db_session:
            #self.db_session.commit()
            self.db_session.close()
            self.Session.remove()

    def run_with_db(self, func, *args, **kwargs):
        db_session = self.get_db_session()

        v = func(db_session, *args, **kwargs)
        return v

    def write_web_ret(self, ret):

        def _parse_object(ret):
            if isinstance(ret, Entity):
                ret = ret.toDict()
                
            if isinstance(ret, list):
                l = []
                for v in ret:
                    l.append(_parse_object(v))
                return l
            elif isinstance(ret, dict):
                d = {}
                for k, v in ret.iteritems():
                    d[k] = _parse_object(v)
                return d
            elif isinstance(ret, datetime.datetime):
                return dtutils.dt_to_str(ret)
            else:
                return ret
            
        ret = _parse_object(ret)
        #print ret
        self.write(json.dumps(ret))
        self.finish()

    def write_error(self, status_code, **kwargs):
        exc_info = kwargs['exc_info']
        traceback.print_exception(*exc_info)
        
        err_msg = u"服务器开小差啦, 请稍后再试"

        if status_code == 404:
            err_msg = u"对不起, 您访问的页面不存在"
            
        data = { 'err_msg' : err_msg }
        
        self.render('mouse/error.tpl', **data)

    def render(self, template_name, **kwargs):
        kwargs["app_config"] = self.app_config
        kwargs["db_session"] = self.get_db_session()
        kwargs['handler'] = self
        return RequestHandler.render(self, template_name, **kwargs)
    
def seed_user_token_cookie(cookie_name = None):

    def _seed(func):
        def wrapper(*args, **kwargs):
            req = args[0]
            if isinstance(req, BaseHandler):
                if not req.get_cookie(cookie_name):
                    req.set_cookie(cookie_name, models_uuids.gen_uuid())

            return func(*args, **kwargs)

        return wrapper

    if not cookie_name:
        cookie_name = const.COOKIE_USER_TOKEN

    return _seed


def web_ret():
    return dict(
        err_code = 0,
        err_msg  = '',
        result   = {},
        )
