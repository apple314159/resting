#!/usr/bin/env python3
#
# HTTP REST testing framework
#
# Inspiration taken from:
# https://github.com/chitamoor/Rester
#

# prerequisties:
# * yaml - https://pyyaml.org/
# * jsonschema - https://github.com/Julian/jsonschema
# * requests - https://github.com/requests/requests
# * requests_toolbelt - https://github.com/requests/toolbelt

import copy
import sys
import time
import yaml
import json
from jsonschema import validate, ValidationError
import xml.etree.ElementTree as ET
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

try:
    from bs4 import BeautifulSoup
except ImportError:
    def BeautifulSoup(**kw):
        return None

from future.utils import iteritems
from past.builtins import basestring

_schema = \
{
    "type": "object",
    "properties": {
        "name": {
            "type": "string"
        },
        "globals": {
            "type": "object",
            "properties": {
                "env": {
                    "type": "object"
                }
            }
        },
        "testSteps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string"
                    },
                    "url": {
                        "type": "string",
                    },
                    "method": {
                        "enum": ["get", "post", "put", "delete", "patch", "options"]
                    },
                    "headers": {
                        "type": "object"
                    },
                    "auth": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "string",
                        }
                    },
                    "data": {
                        "type": "object"
                    },
                    "json": {
                        "type": "object"
                    },
                    "params": {
                        "type": "object"
                    },
                    "response": {
                        "type": "object",
                    }
                }
            }
        },
        #"required": [ "name", "testSteps"]
    }
}

def env_update(o, e):
    for (k, v) in iteritems(o):
        if isinstance(v, basestring):
            try:
                o[k] = v.format(**e)
            except (KeyError, ValueError):
                pass
        elif isinstance(v, dict):
            env_update(v,e)
        elif isinstance(v, list):
            for i,w in enumerate(v):
                if isinstance(w, basestring):
                    try:
                        v[i] = w.format(**e)
                    except KeyError:
                        pass
                elif isinstance(w, dict):
                    env_update(w,e)
    return o

def run_test_case(filename, env):
    try:
        of = open(filename)
    except IOError as e:
        print("Error loading testcase:", e)
        return None
    if filename.endswith('.json'):
        try:
            tc = json.load(of)
        except ValueError as e:
            print("Invalid json: "+filename+" - "+str(e))
            return None
    elif filename.endswith('.yaml'):
        try:
            tc = yaml.load(of)
        except yaml.YAMLError as e:
            print("Invalid yaml: "+filename+" - "+str(e))
            return None
    else:
            print("Unsupported extension")
            return None
    try:
        validate(tc, _schema)
    except ValidationError as e:
        print("Invalid testcase: bad json -", e)
        return None

    # add globals to the environment if they don't already exist
    if tc.get("globals",None) != None:
        tmp_env = tc["globals"].get("env",tc["globals"].get("variables",None))
        if tmp_env != None:
            env.update(tmp_env.items())

    #print(env)
    print(tc.get("name",""))

    s = requests.Session()
    for current_step in tc.get("testSteps", []):

        if current_step.get("sleep",None) != None:
            time.sleep(step.get("sleep",1))
            continue
        if current_step.get("skip",None) != None:
            continue

        if current_step.get("apiUrl",current_step.get("url",None)) == None:
            continue

        env['$step'] = env.get('$step', 0) + 1
        env['$repeat'] = current_step.get("repeat",1)
        for count in range(env["$repeat"]):
            step = copy.deepcopy(current_step)
            env['$count'] = count
            env_update(step, env)

            print("\t"+step.get("name",str(env['$step'])))
            a = step.get("auth", None)
            if a:
                a = (a[0], a[1])

            hdrs=step.get("headers", {})
            payload=step.get("data", None)
            if not payload:
                formlist = step.get("form", None)
                if formlist:
                    fields = {}
                    for fl in formlist:
                        for f, v in iteritems(fl):
                            if isinstance(v, list):
                                fields[f] = (v[0], open(v[0],"rb"), v[1])
                            else:
                                fields[f] = v
                    if fields:
                        payload = MultipartEncoder( fields=fields )
                        hdrs['Content-Type'] = payload.content_type

            if not step.get("cookies", True):
                s.cookies.clear()
            url = step.get("url", step.get("apiUrl"))
            try:
                r = s.request(step["method"], url,
                    params=step.get("params",None),
                    data=payload,
                    json=step.get("json",None),
                    headers=hdrs,
                    auth=a)
            except requests.exceptions.RequestException as e:
                print("\t\tCommunications error: {} failed {}.".format(step["method"], e))
                return None

            rjson = None
            rxml = None
            soup = None
            if 'content-type' in r.headers:
                if r.headers['content-type'][:16] == "application/json":
                    try:
                        rjson = r.json()
                    except ValueError as e:
                        pass
                elif r.headers['content-type'][:9] == "text/html":
                    soup = BeautifulSoup(r.text, 'lxml')
                elif r.headers['content-type'][:8] == "text/xml":
                    try:
                        rxml = ET.fromstring(r.content)
                    except ET.ParseError as e:
                        pass
            if step.get("asserts",None) != None:
                o = step["asserts"].get("headers",None)
                if o != None:
                    for k, v in o.items():
                        if v != r.headers[k]:
                            print("\t\tHeader mismatch! expected %s received: %s" %(v, r.headers[k]))
                            return None

                o = step["asserts"].get("reply",None)
                if o != None:
                    stat = o.get("status_code",None)
                    if stat != None and stat != r.status_code:
                        print("\t\tHTTP status mismatch: expected %s received: %s - %s" %(stat, r.status_code, r.reason))
                        return None
                    if o.get("exec",None) != None:
                        cmd = o.get("exec",None)
                        try:
                            ns = {"execErr": None, "r": r, "recv_json": rjson, "rjson": rjson, "rxml": rxml, "soup": soup}
                            exec(cmd, globals(), ns)
                        except ValueError as e:
                            print('Failed to exec "%s": %s'%(cmd,e))
                            return None
                        if ns["execErr"] != None:
                            return None

            newenv  = step.get("setenv",None)
            if newenv != None:
                for k, v in iteritems(newenv):
                    key = k.format(**env)
                    try:
                        env[key] = eval(v,{},locals())
                    except Exception as e:
                        print('Failed to evaluate (%s) %s'%(v,e))
                        #return None

                #env.update(newenv)
    return env

def run_yaml(args):
    files = []
    env = {}
    for a in args.args:
        e = a.split('=')
        if len(e) == 1:
            files.append(a)
        else:
            env[e[0]] = e[1]

    for f in files:
        run_test_case(f, env)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='HTTP REST testing framework.')
    parser.add_argument('-v', '--verbose', action='store_true')

    parser.add_argument('args', type=str, nargs = '*', default=None)

    args = parser.parse_args()

    run_yaml(args)

# vim:set et ts=4 sw=4 ft=python:
