import sys
import os
import xml.etree.ElementTree as ET
from pathlib import Path
import re
import json
import codecs
import threading
import time
from operator import itemgetter
from multiprocessing import Array

start_time = time.time()
prev_time  = start_time
prev_cnt   = 0

root_dir = os.path.dirname( os.path.abspath(__file__) ).replace('\\', '/') + '/..'

taxonomy_tmpl = root_dir + '/data/EDINET/taxonomy/%s/タクソノミ/taxonomy/'

xsd_dics = {}
label_dics = {}

url2path = {}
xbrl_idx = 0
xbrl_basename = None

dmp_cnt = {}
ctx_cnt = {}
obj_cnt = {}
join_cnt = {}

def addCnt(dic, key):
    if key in dic:
        dic[key] += 1
    else:
        dic[key] = 1

def logCnt(inf, name, dic):
    for k, v in dic.items():
        inf.logf.write('%s %s %d\n' % (name, time_names[k], v) )

edinet_json_dic = {}

url2path_lock = threading.Lock()

label_role = "http://www.xbrl.org/2003/role/label"
verboseLabel_role = "http://www.xbrl.org/2003/role/verboseLabel"

type_dic = {
    "xbrli:stringItemType" : "文字列",
    "xbrli:booleanItemType" : "ブール値",
    "xbrli:dateItemType" : "日付",
    "xbrli:nonNegativeIntegerItemType" : "非負整数",
    "nonnum:textBlockItemType" : "テキストブロック",
    "xbrli:monetaryItemType" : "金額",
    "num:perShareItemType" : "一株当たり金額",
    "num:percentItemType" : "割合(%)",
    "xbrli:decimalItemType" : "小数",
    "xbrli:sharesItemType" : "株数",
    "nonnum:domainItemType" : "ドメイン",
    "xbrli:pureItemType" : "純粋型"
}

time_names_list = [
    ("FilingDateInstant"    , "提出日時点"),
    ("CurrentYearInstant"   ,"当期連結時点"),
    ("CurrentYearDuration"  , "当期連結期間"),
    ("CurrentQuarterInstant", "当四半期会計期間連結時点"),
    ("CurrentQuarterDuration", "当四半期会計期間連結期間"),
    ("CurrentYTDDuration"   , "当四半期累計期間連結期間"),
    ("Prior1YTDDuration"    , "前年度同四半期累計期間連結期間"),
    ("Prior1QuarterInstant" , "前年度同四半期会計期間連結時点"),
    ("Prior1QuarterDuration", "前年度同四半期会計期間連結期間"),
    ("Prior1YearInstant"    , "前期連結時点"),
    ("Prior1YearDuration"   , "前期連結期間"),
    ("Prior2YearInstant"    , "前々期連結時点"),
    ("Prior2YearDuration"   ,"前々期連結期間"),
    ("Prior3YearInstant"    ,"3期前連結時点"),
    ("Prior3YearDuration"   ,"3期前連結期間"),
    ("Prior4YearInstant"    ,"4期前連結時点"),
    ("Prior4YearDuration"   ,"4期前連結期間"),
    ("Prior2InterimInstant" , "Prior2InterimInstant"),
    ("InterimInstant"       , "InterimInstant"),
    ("InterimDuration"      , "InterimDuration"),
    ("Prior1InterimInstant" , "Prior1InterimInstant"),
    ("Prior1InterimDuration", "Prior1InterimDuration"),
    ("Prior2InterimDuration", "Prior2InterimDuration"),
    ("Prior5YearInstant"    , "Prior5YearInstant"),
    ("Prior5YearDuration"   , "Prior5YearDuration"),
]

time_names_order = [ x[0] for x in time_names_list ]

time_names = dict(x for x in time_names_list )

def findObj(v, key, val):
    for x in v:
        if x[key] == val:
            return x
    return None

def copyLabel(dst, src):
    dst['name']          = src['name']
    dst['label']         = src['label']
    dst['verbose_label'] = src['verbose_label']


def cloneItem(inf, obj, cnt, idx):
    union = { 'type':obj['type'], 'text': [None] * cnt }
    copyLabel(union, obj)

    union['text'][idx] = obj['text']

    if obj['type'] == "金額" and obj['label'] == '原材料及び貯蔵品':
        inf.logf.write('clon:%s %s %d %s\n' % (obj['label'], obj['text'], idx, time_names[inf.time_name]))
        addCnt(join_cnt, inf.time_name)

    union['children'] = [ cloneItem(inf, x, cnt, idx) for x in obj['children'] ]

    return union


def joinItem(inf, union, obj, cnt, idx):
    union['text'][idx] = obj['text']

    if obj['type'] == "金額" and obj['label'] == '原材料及び貯蔵品':
        inf.logf.write('join:%s %s %d %s\n' % (obj['label'], obj['text'], idx, time_names[inf.time_name]))
        addCnt(join_cnt, inf.time_name)


    union_children = union['children']
    for child in obj['children']:
        union_child = findObj(union_children, 'name', child['name'])
        if union_child is None:
            union_children.append( cloneItem(inf, child, cnt, idx) )
        else:
            joinItem(inf, union_child, child, cnt, idx)

    return union

def joinAxis(inf, union_axis, axis, cnt, idx):
    assert 'name' in axis and 'name' in union_axis
    assert union_axis['name'] == axis['name']
    assert 'members' in axis and 'members' in union_axis

    union_members = union_axis['members']
    for member in axis['members']:
        union_member = findObj(union_members, 'name', member['name'])
        if union_member is None:
            union_member = {}
            copyLabel(union_member, member)
            union_members.append( joinObj(inf, union_member, member, cnt, idx) )
        else:
            joinObj(inf, union_member, member, cnt, idx)

    return union_axis

def joinObj(inf, union, obj, cnt, idx):
    if 'time' in obj:
        if 'time' in union:
            assert union['time'] == obj['time']
        else:
            union['time'] = obj['time']

    if 'axes' in obj:
        if 'axes' in union:
            union_axes = union['axes']
        else:
            union_axes = []
            union['axes'] = union_axes

        for axis in obj['axes']:
            union_axis = findObj(union_axes, 'name', axis['name'])
            if union_axis is None:
                union_axis = { 'members':[] }
                copyLabel(union_axis, axis)
                union_axes.append( union_axis )

            joinAxis(inf, union_axis, axis, cnt, idx)

    if 'values' in obj:
        if 'values' in union:
            union_values = union['values']
            for value in obj['values']:
                union_value = findObj(union_values, 'name', value['name'])
                if union_value is None:
                    union_values.append( cloneItem(inf, value, cnt, idx) )
                else:
                    joinItem(inf, union_value, value, cnt, idx)

        else:
            union['values'] = [ cloneItem(inf, x, cnt, idx) for x in obj['values'] ]

    return union



class Item:
    def __init__(self, ctx, ele, text):
        self.ctx     = ctx
        self.element = ele
        self.text    = text
        self.children = []

    def itemToObj(self, inf, ancestors):
        if self in ancestors:
            print(xbrl_basename)
            for x in ancestors + [self]:
                name, label, verbose_label = x.element.getLabel()
                print(label)

        assert not self in ancestors
        ancestors.append(self)

        ele = self.element
        text = self.text

        if text is None:
            text = 'null-text'
        else:
            if ele.type == "テキストブロック":
                text = "省略"
            elif ele.type == '文字列':
                text = text.replace('\n', ' ')

                if 100 < len(text):
                    text = "省略:" + text

        name, label, verbose_label = ele.getLabel()

        obj = { 'type': ele.type, 'name':name, 'label':label, 'verbose_label':verbose_label, 'text': text }
        obj['children'] = [ item2.itemToObj(inf, ancestors) for item2 in self.children ]

        if ele.type == "金額" and label == '原材料及び貯蔵品':
            inf.logf.write('obj :%s %s %s\n' % (label, text, time_names[self.ctx.time]))
            addCnt(obj_cnt, self.ctx.time)

        ancestors.pop()
        return obj


class Context:
    def __init__(self):
        self.time       = None
        self.startDate = None
        self.endDate = None
        self.instant = None

        self.axis_eles  = []
        self.member_eles = []


class ContextNode:
    def __init__(self):
        self.time       = None
        self.startDate = None
        self.endDate = None
        self.instant = None
        self.axes  = []
        self.member_ele = None
        self.values  = []

    def toObj(self, inf):
        obj = {}
        if self.time is not None:
            obj['time'] = self.time

        if self.member_ele is not None:

            name, label, verbose_label = self.member_ele.getLabel()
            obj['name']          = name
            obj['label']         = label
            obj['verbose_label'] = verbose_label

        if len(self.axes) != 0:
            axes = []
            obj['axes'] = axes
            for axis in self.axes:
                dt = { 'members': [ nd.toObj(inf) for nd in axis.members ] }

                dt['name']          = axis.name
                dt['label']         = axis.label
                dt['verbose_label'] = axis.verbose_label

                axes.append(dt)

        if len(self.values) != 0:

            obj['values'] = [ item.itemToObj(inf, []) for item in self.values ]

        return obj
class Axis:
    def __init__(self, name, label, verbose_label):
        self.name = name
        self.label = label
        self.verbose_label = verbose_label
        self.members = []

class Element:
    def __init__(self):
        self.url  = None
        self.name = None
        self.id   = None
        self.type = None
        self.labels = {}
        self.calcTo = []
        self.sorted = False

    def getLabel(self):
        verbose_label = None
        label = None

        if verboseLabel_role in self.labels:
            verbose_label = self.labels[verboseLabel_role]

        if label_role in self.labels:
            label = self.labels[label_role]

        if verbose_label is None and label is None:
            assert self.url in ['http://www.xbrl.org/2003/instance', 'http://www.w3.org/2001/XMLSchema']

        return self.name, label, verbose_label


class Calc:
    def __init__(self, to_el, role, order, weight):
        self.to = to_el
        self.role = role
        self.order = order
        self.weight = weight

class Inf:
    __slots__ = [ 'cpu_count', 'cpu_id', 'cur_dir', 'local_context_dic', 'local_top_context_nodes', 'local_ns_dic', 'local_xsd_dics', 'local_url2path', 'local_xsd_url2path', 'logf', 'progress', 'time_name' ]

    def __init__(self):
        self.cur_dir = None
        self.local_xsd_url2path = None
        self.local_xsd_dics = None
        self.time_name = None

def splitUrlLabel(text):
    if text[0] == '{':
        i = text.index('}')
        url = text[1:i]
        label = text[i+1:]

        return url, label
    
    return None, None

def getAttribs(el):
    attr = {}
    for k, v in el.attrib.items():
        attr_url, attr_label = splitUrlLabel(k)
        attr[attr_label] = v

    return attr

def parseElement(el):

    id = el.get("id")
    text  = el.text

    if el.tag[0] == '{':
        i = el.tag.index('}')
        url = el.tag[1:i]
        label = el.tag[i+1:]
    else:
        url = None
        label = None

    return id, url, label, text

def normUrl(url):
    if not url.endswith('.xsd') and url.startswith('http://disclosure.edinet-fsa.go.jp/taxonomy/'):
        v = url.split('/')

        name_space = v[4]
        yymmdd     = v[5]
        name_cor   = v[6]

        # '/2013-08-31/タクソノミ/taxonomy/jpdei/2013-08-31/jpdei_cor_2013-08-31.xsd'

        file_name = name_cor + "_" + yymmdd + '.xsd'
        url2 = '/'.join(v[:6]) + '/' + file_name

        return url2

    elif url in [
         'http://xbrl.ifrs.org/taxonomy/2015-03-11/ifrs-full',
         'http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full',
         'http://xbrl.ifrs.org/taxonomy/2014-03-05/full_ifrs/full_ifrs-cor_2014-03-05.xsd'
        ]:
        return 'http://xbrl.ifrs.org/taxonomy/2015-03-11/full_ifrs/full_ifrs-cor_2015-03-11.xsd'

    else:
        return url

def NoneStr(x):
    if x is None:
        return ""
    else:
        return x

def getTitleNsLabel(inf, text):

    v1 = text.split(':')
    assert v1[0] in inf.local_ns_dic
    ns_url = inf.local_ns_dic[v1[0]]
    label      = v1[1]

    ele = getElement(inf, ns_url, label)

    return ele

def ReadLabel(el, xsd_dic, loc_dic, resource_dic):
    if el.tag[0] == '{':
        i = el.tag.index('}')
        url = el.tag[1:i]
        label = el.tag[i+1:]

        if label == "loc":

            attr = getAttribs(el)
            assert 'href' in attr and 'label' in attr
            v = attr['href'].split('#')
            assert len(v) == 2
            loc_dic[ attr['label'] ] = v[1]

        elif label == "label":

            attr = getAttribs(el)
            if 'label' in attr and 'role' in attr:
                if attr['role'] in [ label_role, verboseLabel_role ]:
                    resource_dic[ attr['label'] ] = { 'role':attr['role'], 'text': el.text }

            id = el.get("id")
            if id is None:
                # {http://www.xbrl.org/2003/linkbase}label

                return
            # assert id.startswith("label_")

        elif label == "labelArc":
            if xsd_dic is not None:
                attr = getAttribs(el)

                if 'from' in attr and 'to' in attr and attr['to'] in resource_dic:
                    if attr['from'] in loc_dic and loc_dic[ attr['from'] ] in xsd_dic :
                        ele = xsd_dic[ loc_dic[ attr['from'] ] ]
                        res = resource_dic[ attr['to'] ]
                        ele.labels[ res['role'] ] = res['text']
                    elif attr['from'] in xsd_dic:
                        ele = xsd_dic[ attr['from'] ]
                        res = resource_dic[ attr['to'] ]
                        ele.labels[ res['role'] ] = res['text']

    for child in el:
        ReadLabel(child, xsd_dic, loc_dic, resource_dic)


def readContext(inf, el, parent, ctx):
    id, url, label, text = parseElement(el)

    if label == "identifier":
        assert parent == "entity"

    elif label == "startDate":
        assert parent == "period"
        ctx.startDate = text
    elif label == "endDate":
        assert parent == "period"
        ctx.endDate = text
    elif label == "instant":
        assert parent == "period"
        ctx.instant = text

    elif label == "explicitMember":
        assert parent == "scenario"

        dimension = el.get("dimension")
        dimension_ele = getTitleNsLabel(inf, dimension)

        if not dimension_ele in ctx.axis_eles:
            ctx.axis_eles.append(dimension_ele)


        member_ele = getTitleNsLabel(inf, text)

        ctx.member_eles.append(member_ele)

    else:
        assert label in [ "context", "entity", "period", "scenario" ]

    for child in el:
        readContext(inf, child, label, ctx)


def setChildren(inf, ctx):
    if len(ctx.axes) != 0:
        for axis in ctx.axes:
            for nd in axis.members:
                setChildren(inf, nd)

    if len(ctx.values) == 0:
        return

    top_items = list(ctx.values)
    for item in ctx.values:

        if not item.element.sorted:
            item.element.sorted = True
            item.element.calcTo = sorted(item.element.calcTo, key=lambda x: x.order)
            
        child_elements = [ x.to for x in item.element.calcTo ]
        sum_items = [ x for x in ctx.values if x.element in child_elements ]
        for sum_item in sum_items:
            if sum_item in top_items:
                item.children.append(sum_item)
                top_items.remove(sum_item)

        if item.element.type == "金額":
            name, label, verbose_label = item.element.getLabel()
            if label == '原材料及び貯蔵品':
                inf.logf.write('ctx :%s %s %s\n' % (label, item.text, time_names[ctx.time]))
                addCnt(ctx_cnt, ctx.time)

    ctx.values = top_items

def readCalcArcs(xsd_dic, locs, arcs):
    for el2 in arcs:
        attr2 = getAttribs(el2)
        role = attr2['arcrole']
        if role == 'http://www.xbrl.org/2003/arcrole/summation-item':
            order = el2.get('order')
            weight = el2.get('weight')
            assert order is not None and weight is not None
            order = float(order)

            from_label = attr2['from'] 
            to_label = attr2['to'] 
            assert from_label is not None and to_label is not None

            from_el = locs[from_label] 
            if to_label in locs:
                to_el = locs[to_label] 
            else:
                to_el = xsd_dic[to_label]
            assert from_el is not None and to_el is not None

            if not to_el in [ x.to for x in from_el.calcTo ]:
                from_el.calcTo.append( Calc(to_el, role, order, weight) )

#--------------------------------------------------------------------------------------------------------------

def ReadSchema(inf, is_local, xsd_path, el, xsd_dic):
    url, label = splitUrlLabel(el.tag)

    if label == 'schema':
        target_ns = el.get('targetNamespace')
        target_ns = normUrl(target_ns)
        if is_local:
            inf.local_xsd_url2path[target_ns] = xsd_path
            inf.local_xsd_dics[target_ns] = xsd_dic
        else:
            xsd_dics[target_ns] = xsd_dic

        attr = getAttribs(el)
    elif label == "element":

        ele = Element()
        ele.url  = url
        ele.name = el.get("name")
        ele.id   = el.get("id")

        type = el.get("type")
        if type in type_dic:
            ele.type = type_dic[ type ]
        else:
            ele.type = type

        xsd_dic[ele.name] = ele

        if ele.id is not None:
            xsd_dic[ele.id] = ele
                
    for child in el:
        ReadSchema(inf, is_local, xsd_path, child, xsd_dic)

def parseNsUrl(inf, ns_url):

    if ns_url.startswith("http://disclosure.edinet-fsa.go.jp/taxonomy/"):
        # http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2017-02-28/jpcrp_cor

        v2 = ns_url.split('/')
        name_space = v2[4]
        yymmdd     = v2[5]
        name_cor   = v2[6]

        # '/2013-08-31/タクソノミ/taxonomy/jpdei/2013-08-31/jpdei_cor_2013-08-31.xsd'

        if ns_url.endswith('.xsd'):
            file_name = os.path.basename(ns_url)
        else:
            file_name = name_cor + "_" + yymmdd + '.xsd'
        xsd_path = (taxonomy_tmpl % yymmdd) + name_space + '/' + yymmdd + '/' + file_name
        label_path = (taxonomy_tmpl % yymmdd) + name_space + '/' + yymmdd + '/label/' + name_space + "_" + yymmdd + '_lab.xml'

    elif ns_url.startswith("http://disclosure.edinet-fsa.go.jp/"):
        # http://disclosure.edinet-fsa.go.jp/ifrs/q2r/001/E00949-000/2016-09-30/01/2016-11-04
        # http://disclosure.edinet-fsa.go.jp/jpcrp040300/q2r/001/E00949-000/2016-09-30/01/2016-11-04
        # jpcrp040300-q2r-001_E31382-000_2015-07-31_01_2015-09-14.xsd

        v = ns_url[len("http://disclosure.edinet-fsa.go.jp/"):].split('/')
        name = '-'.join(v[:3]) + '_' + '_'.join(v[3:])

        base_path = "%s/%s" % (inf.cur_dir, name )
        xsd_path   = base_path + '.xsd'
        label_path = base_path + '_lab.xml'

    elif ns_url.startswith("http://xbrl.ifrs.org/taxonomy/"):
        if ns_url == 'http://xbrl.ifrs.org/taxonomy/2015-03-11/full_ifrs/full_ifrs-cor_2015-03-11.xsd':
            ns_url = 'http://xbrl.ifrs.org/taxonomy/2015-03-11/ifrs-full'
        elif ns_url == 'http://xbrl.ifrs.org/taxonomy/2014-03-05/full_ifrs/full_ifrs-cor_2014-03-05.xsd':
            ns_url = 'http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full'

        if not ns_url in [ 
            'http://xbrl.ifrs.org/taxonomy/2015-03-11/ifrs-full', 
            'http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full'            
            ]:
            print(ns_url)


        assert ns_url in [ 
            'http://xbrl.ifrs.org/taxonomy/2015-03-11/ifrs-full', 
            'http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full' 
        ]

        ifrs_path = root_dir + "/data/IFRS/IFRST_%s/full_ifrs/full_ifrs-cor_%s.xsd"

        v = ns_url.split('/')
        yyyymmdd = v[4]

        xsd_path = ifrs_path % (yyyymmdd, yyyymmdd)
        if not os.path.exists(xsd_path):
            # 2014-03-05が無いので、2015-03-11で代用

            xsd_path = ifrs_path % ('2015-03-11', '2015-03-11')
            assert os.path.exists(xsd_path)

        if yyyymmdd == '2015-03-11':
            label_path = root_dir + '/data/IFRS/ja/Japanese-Taxonomy-2015/full_ifrs/labels/lab_full_ifrs-ja_2015-03-11.xml'
        elif yyyymmdd == '2014-03-05':
            label_path = root_dir + '/data/IFRS/ja/Japanese-Taxonomy-2014/full_ifrs/labels/lab_full_ifrs-ja_2014-03-05_rev_2015-03-06.xml'
        else:
            assert False

    elif ns_url == "http://www.xbrl.org/2003/instance":
        xsd_path = root_dir + "/data/IFRS/xbrl-instance-2003-12-31.xsd"
        label_path = None

    else:        
        assert ns_url in [ "http://www.xbrl.org/2003/instance", "http://www.xbrl.org/2003/linkbase" ]

        return None, None

    if xsd_path is not None:
        if inf.cur_dir is not None and xsd_path.startswith(inf.cur_dir):
            if ns_url in inf.local_url2path:
                assert inf.local_url2path[ns_url] == xsd_path
            else:
                inf.local_url2path[ns_url] = xsd_path
        else:
            url2path_lock.acquire()
            
            if ns_url in url2path:
                assert url2path[ns_url] == xsd_path
            else:
                url2path[ns_url] = xsd_path
            
            url2path_lock.release()
    
    elif inf.local_xsd_url2path is not None and ns_url in inf.local_xsd_url2path:
        assert inf.local_xsd_url2path[ns_url] == xsd_path


    return xsd_path, label_path

def makeContext(inf, el, id):
    ctx = Context()

    readContext(inf, el, None, ctx)
    assert len(ctx.axis_eles) == len(ctx.member_eles)

    if len(ctx.axis_eles) == 0:

        if not id in time_names:
            print(id, '-----------------------------------------------------')
        # assert id in time_names
        ctx.time = id

    else:

        k = id.find('_')
        assert k != -1
        s = id[:k]
        if not s in time_names:
            print(s, '-----------------------------------------------------')
        # assert s in time_names
        ctx.time = s

    v = [ x for x in inf.local_top_context_nodes if x.time == ctx.time ]
    if len(v) != 0:
        assert len(v) == 1
        nd = v[0]
    else:
        nd = ContextNode()
        nd.time      = ctx.time
        nd.startDate = ctx.startDate
        nd.endDate   = ctx.endDate
        nd.instant   = ctx.instant

        inf.local_top_context_nodes.append(nd)

    leaf_nd = nd
    for axis_ele, member_ele in zip(ctx.axis_eles, ctx.member_eles):
        name, label, verbose_label = axis_ele.getLabel()
        axis_list = [ x for x in nd.axes if x.name == name ]
        if len(axis_list) == 0:
            axis = Axis(name, label, verbose_label)
            nd.axes.append(axis)

        else:
            assert len(axis_list) == 1            
            axis = axis_list[0]

        member_list = [ x for x in axis.members if x.member_ele == member_ele ]
        if len(member_list) != 0:
            assert len(member_list) == 1

            leaf_nd = member_list[0]
        else:

            leaf_nd = ContextNode()

            leaf_nd.time      = ctx.time
            leaf_nd.member_ele = member_ele
            axis.members.append(leaf_nd)

    assert not id in inf.local_context_dic
    inf.local_context_dic[id] = leaf_nd


def getNameSpace(inf, path):
    f = open(path)
    for line in f:
        if line.find("xmlns:") != -1:
            k1 = 0
            while True:
                k1 = line.find("xmlns:", k1)
                if k1 == -1:
                    break
                k1 += 6

                k2 = line.find("=", k1)
                name = line[k1:k2]

                assert line[k2 + 1] == '"'
                k3 = line.find('"', k2 + 2)
                url = line[k2 + 2:k3]

                inf.local_ns_dic[name] = url                
                
            break
    f.close()

def GetSchemaLabelDic(inf, url):
    url = normUrl(url)
    xsd_path, label_path = parseNsUrl(inf, url)

    xsd_dic = None

    if xsd_path is not None:
        if inf.local_xsd_dics is not None and url in inf.local_xsd_dics:
            xsd_dic = inf.local_xsd_dics[url]

        else:

            if url in xsd_dics:
                xsd_dic = xsd_dics[url]

            elif os.path.exists(xsd_path):
                xsd_dic = {}

                xsd_tree = ET.parse(xsd_path)
                xsd_root = xsd_tree.getroot()
                ReadSchema(inf, False, xsd_path, xsd_root, xsd_dic)
                assert xsd_dics[url] == xsd_dic

    if label_path is not None:
        if label_path.startswith(inf.cur_dir):
            pass

        else:

            if label_path in label_dics:
                pass

            elif os.path.exists(label_path):

                label_tree = ET.parse(label_path)
                label_root = label_tree.getroot()

                resource_dic = {}
                loc_dic = {}
                ReadLabel(label_root, xsd_dic, loc_dic, resource_dic)

                label_dics[label_path]  = 1


    return xsd_dic

def getElement(inf, url, label):
    xsd_dic = GetSchemaLabelDic(inf, url)

    assert xsd_dic is not None and label in xsd_dic
    ele = xsd_dic[label]

    return ele

def dumpSub(inf, el):

    id, url, label, text = parseElement(el)

    if url == "http://www.xbrl.org/2003/instance" and label == "context":

        makeContext(inf, el, id)
        return False

    # if url in [ "http://www.xbrl.org/2003/instance", "http://www.xbrl.org/2003/linkbase" ]:
    if url in [ "http://www.xbrl.org/2003/linkbase" ]:
        pass
    else:

        ele = getElement(inf, url, label)

        assert el.tag[0] == '{'

        context_ref = el.get("contextRef")
        # assert context_ref is not None
        if ele.type is None or context_ref is None:
            return True

        assert context_ref in inf.local_context_dic
        ctx = inf.local_context_dic[context_ref]

        item = Item(ctx, ele, text)
        ctx.values.append(item)

        if ele.type == "金額":
            name, label, verbose_label = ele.getLabel()
            if label == '原材料及び貯蔵品':
                inf.logf.write('dmp :%s %s %s\n' % (label, text, time_names[ctx.time]))
                addCnt(dmp_cnt, ctx.time)

    return True

def dump(inf, el):
    go_down = dumpSub(inf, el)

    if go_down:
        for child in el:
            dump(inf, child)

def readCalcSub(inf, el, xsd_dic, locs, arcs):
    url, label = splitUrlLabel(el.tag)

    if label == 'calculationLink':
        attr = getAttribs(el)
        for el2 in el:
            url2, label2 = splitUrlLabel(el2.tag)
            if label2 in [ 'loc', 'calculationArc' ]: 
                if label2 == 'loc': 
                    attr2 = getAttribs(el2)
                    v = attr2['href'].split('#')
                    if v[0].startswith('http://'):
                        xsd_dic2 = GetSchemaLabelDic(inf, v[0])

                    else:
                        xsd_dic2 = xsd_dic
                    assert v[1] in xsd_dic2
                    locs[ attr2['label'] ] = xsd_dic2[ v[1] ]

                elif label2 == 'calculationArc':
                    arcs.append(el2)

    else:
        for child in el:
            readCalcSub(inf, child, xsd_dic, locs, arcs)

def readCalc(inf):
    name_space = 'jppfs'
    name_cor = 'jppfs_cor'
    for yymmdd in [ '2018-02-28' ]:
        xsd_base = (taxonomy_tmpl % yymmdd) + name_space + '/' + yymmdd
        xsd_path = xsd_base + '/' + name_cor + "_" + yymmdd + '.xsd'

        xsd_dic = {}

        ReadSchema(inf, False, xsd_path, ET.parse(xsd_path).getroot(), xsd_dic)

        for xml_path in Path(xsd_base).glob('r/*/*.xml'):
            xml_path = str(xml_path).replace('\\', '/')
            locs = {}
            arcs = []
            readCalcSub(inf, ET.parse(xml_path).getroot(), xsd_dic, locs, arcs)
            readCalcArcs(xsd_dic, locs, arcs)

def readXbrl(inf, category_name, public_doc):
    global xbrl_idx, prev_time, prev_cnt, xbrl_basename

    xbrl_list = list( public_doc.glob("*.xbrl") )
    for p in xbrl_list:

        xbrl_path = str(p)
        xbrl_basename = os.path.basename(xbrl_path)
        inf.logf.write('%s ---------------------------------------------------\n' % xbrl_basename)

        if xbrl_basename in [ 'jpcrp040300-q3r-001_E27273-000_2015-12-31_01_2016-02-12.xbrl', 'jpcrp030000-asr-001_E00273-000_2015-03-31_01_2015-06-19.xbrl', 'jpcrp030000-asr-001_E00273-000_2014-03-31_01_2014-06-20.xbrl' ]:
            print('循環参照をスキップ', xbrl_basename)
            continue

        if xbrl_basename.startswith('ifrs-'):
            assert len(xbrl_list) == 2
            continue

        # if xbrl_basename != 'jpcrp040300-q2r-001_E03369-000_2016-09-30_01_2016-11-14.xbrl':
        #     continue

        xbrl_idx += 1
        inf.progress[inf.cpu_id] = xbrl_idx
        if xbrl_idx % 100 == 0:

            cnt = sum(inf.progress)
            lap = "%d" % int(1000 * (time.time() - prev_time) / (cnt - prev_cnt) )
            prev_time = time.time()
            prev_cnt = cnt
            print(inf.cpu_id, lap, cnt, category_name)

        inf.cur_dir = os.path.dirname(xbrl_path).replace('\\', '/')

        inf.local_context_dic = {}
        inf.local_top_context_nodes = []

        inf.local_ns_dic = {}
        inf.local_xsd_dics = {}
        inf.local_url2path = {}
        inf.local_xsd_url2path = {}

        label_cnt = 0

        for local_xsd_path_obj in Path(inf.cur_dir).glob("*.xsd"):
            local_xsd_path_org = str(local_xsd_path_obj)
            local_xsd_path = local_xsd_path_org.replace('\\', '/')

            local_xsd_dic = {}

            ReadSchema(inf, True, local_xsd_path, ET.parse(local_xsd_path).getroot(), local_xsd_dic)

            local_label_path = local_xsd_path[:len(local_xsd_path) - 4] + "_lab.xml"
            if os.path.exists(local_label_path):

                resource_dic = {}
                loc_dic = {}
                ReadLabel(ET.parse(str(local_label_path)).getroot(), local_xsd_dic, loc_dic, resource_dic)
                label_cnt += 1

            local_cal_path = local_xsd_path[:-4] + '_cal.xml'
            if os.path.exists(local_cal_path):
                locs = {}
                arcs = []
                readCalcSub(inf, ET.parse(local_cal_path).getroot(), local_xsd_dic, locs, arcs)
                readCalcArcs(local_xsd_dic, locs, arcs)

        local_label_path_list = list( Path(inf.cur_dir).glob("*_lab.xml") )
        assert len(local_label_path_list) == label_cnt

        getNameSpace(inf, xbrl_path)

        tree = ET.parse(xbrl_path)
        root = tree.getroot()
        dump(inf, root)

        for ctx in inf.local_top_context_nodes:
            setChildren(inf, ctx)

        ctx_objs = []
        for ctx in inf.local_top_context_nodes:
            ctx_objs.append(ctx.toObj(inf))

        json_str = json.dumps(ctx_objs, ensure_ascii=False)

        v1 = [ x for x in ctx_objs if x['time'] == 'FilingDateInstant' ] # 提出日時点
        dt1 = v1[0]
        v2 = [ x for x in dt1['values'] if x['name'] == 'EDINETCodeDEI' ]
        dt2 = v2[0]
        edinet_code = dt2['text']
        end_date = [ x for x in dt1['values'] if x['name'] == 'CurrentPeriodEndDateDEI' ][0]['text'] # 当会計期間終了日
        num_submission = findObj(dt1['values'], 'name', 'NumberOfSubmissionDEI')['text'] # 提出回数

        if edinet_code in edinet_json_dic:
            category_name, json_str_list = edinet_json_dic[edinet_code]

            json_str_list.append( [end_date, num_submission, json_str] )

            revisions = [x for x in json_str_list if x[0] == end_date]
            if 2 <= len(revisions):
                revisions = sorted(revisions, key=lambda x: x[1])
                for x in revisions[:-1]:
                    assert x in json_str_list
                    # json_str_list.remove(x)

        else:
            edinet_json_dic[edinet_code] = (category_name, [[end_date, num_submission, json_str]])

def make_public_docs_list(cpu_count):
    report_path = root_dir + '/data/EDINET/四半期報告書'
    category_edinet_codes = []

    public_docs_list = [ [] for i in range(cpu_count) ]
    for category_dir in Path(report_path).glob("*"):
        category_name = os.path.basename(str(category_dir))

        edinet_codes = []
        category_edinet_codes.append( { 'category_name': category_name, 'edinet_codes': edinet_codes}   )

        for public_doc in category_dir.glob("*/*/XBRL/PublicDoc"):
            xbrl_path_list = list(public_doc.glob('jpcrp*.xbrl'))
            assert len(xbrl_path_list) == 1

            xbrl_path_0 = xbrl_path_list[0]
            xbrl_path_0_basename = os.path.basename(str(xbrl_path_0))
            items = re.split('[-_]', xbrl_path_0_basename)
            edinet_code = items[3]
            char_sum = sum(ord(x) for x in edinet_code)
            cpu_idx  = char_sum % cpu_count

            public_docs_list[cpu_idx].append( [category_name, public_doc] )

            if not edinet_code in edinet_codes:
                edinet_codes.append(edinet_code)

    json_path = "%s/web/json/category_edinet_codes.json" % root_dir
    with codecs.open(json_path, 'w','utf-8') as json_f:
        json.dump(category_edinet_codes, json_f, ensure_ascii=False)

    return category_edinet_codes, public_docs_list


def readXbrlThread(cpu_count, cpu_id, public_docs, progress):
    inf = Inf()
    
    inf.cpu_count = cpu_count
    inf.cpu_id = cpu_id
    inf.progress = progress
    inf.logf =  open('%s/data/log-%d.txt' % (root_dir, cpu_id), 'w', encoding='utf-8')

    for category_name, public_doc in public_docs:
        readXbrl(inf, category_name, public_doc)

    for edinet_code, (category_name, json_str_list) in edinet_json_dic.items():
        json_dir = "%s/web/json/%s" % (root_dir, category_name)
        if not os.path.exists(json_dir):
            os.makedirs(json_dir)

        json_str_list = sorted(json_str_list, key=lambda x: x[0])

        end_date_objs_dic = {}
        for end_date, num_submission, json_str in json_str_list:
            objs = json.loads(json_str)

            for obj in objs:

                if obj['time'] in end_date_objs_dic:
                    end_date_objs_dic[obj['time']].append( (end_date, obj) )
                else:
                    end_date_objs_dic[obj['time']] = [ (end_date, obj) ]

        time_end_dates_unions = []
        for time_name, end_date_objs in end_date_objs_dic.items():
            inf.time_name = time_name
            union = {}
            time_end_dates = []
            for idx, (end_date, obj) in enumerate(end_date_objs):
                time_end_dates.append(end_date)
                joinObj(inf, union, obj, len(end_date_objs), idx)
            
            time_end_dates_unions.append( (time_name, time_end_dates, union) )

        time_end_dates_unions = sorted(time_end_dates_unions, key=lambda x: time_names_order.index(x[0]))

        end_dates = [ x[0] for x in json_str_list ]
        doc = { 'end_dates': end_dates, 'time_objs': time_end_dates_unions }
        with codecs.open('%s/%s.json' % (json_dir, edinet_code), 'w','utf-8') as f:
            json.dump(doc, f, ensure_ascii=False)

    logCnt(inf, 'dmp', dmp_cnt)
    logCnt(inf, 'ctx', ctx_cnt)
    logCnt(inf, 'obj', obj_cnt)
    logCnt(inf, 'join', join_cnt)

    assert len(dmp_cnt) == len(ctx_cnt) and len(dmp_cnt) == len(obj_cnt) and len(dmp_cnt) == len(join_cnt)
    for k, v in dmp_cnt.items():
        assert  ctx_cnt[k] == v and obj_cnt[k] == v and join_cnt[k] == v

    inf.logf.close()
    print('CPU:%d 終了:%d' % (cpu_id, int(time.time() - start_time)) )

inf = Inf()
readCalc(inf)

GetSchemaLabelDic(inf, "http://www.xbrl.org/2003/instance")

if __name__ == '__main__':

    cpu_count = 1
    cpu_id = 0

    progress = Array('i', [0] * cpu_count)
    category_edinet_codes, public_docs_list = make_public_docs_list(cpu_count)

    readXbrlThread(cpu_count, cpu_id, public_docs_list[cpu_id], progress)
