# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import contextlib
import werkzeug.urls
from lxml import etree
from unittest.mock import Mock, MagicMock, patch

import werkzeug

import odoo
from odoo.tools.misc import hmac, DotDict


def werkzeugRaiseNotFound(*args, **kwargs):
    raise werkzeug.exceptions.NotFound()


@contextlib.contextmanager
def MockRequest(
        env, *, routing=True, multilang=True,
        context=None,
        cookies=None, country_code=None, website=None, sale_order_id=None,
        website_sale_current_pl=None,
):
    router = MagicMock()
    match = router.return_value.bind.return_value.match
    if routing:
        match.return_value[0].routing = {
            'type': 'http',
            'website': True,
            'multilang': multilang
        }
    else:
        match.side_effect = werkzeugRaiseNotFound

    if context is None:
        context = {}
    lang_code = context.get('lang', env.context.get('lang', 'en_US'))
    context.setdefault('lang', lang_code)

    request = Mock(
        context=context,
        db=None,
        endpoint=match.return_value[0] if routing else None,
        env=env,
        httprequest=Mock(
            host='localhost',
            path='/hello',
            app=odoo.http.root,
            environ={'REMOTE_ADDR': '127.0.0.1'},
            cookies=cookies or {},
            referrer='',
        ),
        lang=env['res.lang']._lang_get(lang_code),
        redirect=env['ir.http']._redirect,
        session=DotDict(
            geoip={'country_code': country_code},
            debug=False,
            sale_order_id=sale_order_id,
            website_sale_current_pl=website_sale_current_pl,
        ),
        website=website,
        render=lambda *a, **kw: '<MockResponse>',
    )

    with contextlib.ExitStack() as s:
        odoo.http._request_stack.push(request)
        s.callback(odoo.http._request_stack.pop)
        s.enter_context(patch('odoo.http.root.get_db_router', router))

        yield request

# Fuzzy matching tools

def distance(s1="", s2="", limit=4):
    """
    Limited Levenshtein-ish distance (inspired from Apache text common)
    Note: this does not return quick results for simple cases (empty string, equal strings)
        those checks should be done outside loops that use this function.

    :param s1: first string
    :param s2: second string
    :param limit: maximum distance to take into account, return -1 if exceeded

    :return: number of character changes needed to transform s1 into s2 or -1 if this exceeds the limit
    """
    BIG = 100000 # never reached integer
    if len(s1) > len(s2):
        s1, s2 = s2, s1
    l1 = len(s1)
    l2 = len(s2)
    if l2 - l1 > limit:
        return -1
    boundary = min(l1, limit) + 1
    p = [i if i < boundary else BIG for i in range(0, l1 + 1)]
    d = [BIG for _ in range(0, l1 + 1)]
    for j in range(1, l2 + 1):
        j2 = s2[j -1]
        d[0] = j
        range_min = max(1, j - limit)
        range_max = min(l1, j + limit)
        if range_min > 1:
            d[range_min -1] = BIG
        for i in range(range_min, range_max + 1):
            if s1[i - 1] == j2:
                d[i] = p[i - 1]
            else:
                d[i] = 1 + min(d[i - 1], p[i], p[i - 1])
        p, d = d, p
    return p[l1] if p[l1] <= limit else -1

def similarity_score(s1, s2):
    """
    Computes a score that describes how much two strings are matching.

    :param s1: first string
    :param s2: second string

    :return: float score, the higher the more similar
        pairs returning non-positive scores should be considered non similar
    """
    dist = distance(s1, s2)
    if dist == -1:
        return -1
    set1 = set(s1)
    score = len(set1.intersection(s2)) / len(set1)
    score -= dist / len(s1)
    score -= len(set1.symmetric_difference(s2)) / (len(s1) + len(s2))
    return score

def text_from_html(html_fragment):
    """
    Returns the plain non-tag text from an html

    :param html_fragment: document from which text must be extracted

    :return: text extracted from the html
    """
    # lxml requires one single root element
    tree = etree.fromstring('<p>%s</p>' % html_fragment, etree.XMLParser(recover=True))
    return ' '.join(tree.itertext())

def get_base_domain(url, strip_www=False):
    """
    Returns the domain of a given url without the scheme and the www. and the
    final '/' if any.

    :param url: url from which the domain must be extracted
    :param strip_www: if True, strip the www. from the domain

    :return: domain of the url
    """
    if not url:
        return ''

    url = werkzeug.urls.url_parse(url).netloc
    if strip_www and url.startswith('www.'):
        url = url[4:]
    return url


def add_form_signature(html_fragment, env_sudo):
    for form in html_fragment.iter('form'):
        if '/website/form/' not in form.attrib.get('action', ''):
            continue

        existing_hash_node = form.find('.//input[@type="hidden"][@name="website_form_signature"]')
        if existing_hash_node is not None:
            existing_hash_node.getparent().remove(existing_hash_node)
        input_nodes = form.xpath('.//input[contains(@name, "email_")]')
        form_values = {input_node.attrib['name']: input_node for input_node in input_nodes}
        # if this form does not send an email, ignore. But at this stage,
        # the value of email_to can still be None in case of default value
        if 'email_to' not in form_values:
            continue
        elif not form_values['email_to'].attrib.get('value'):
            form_values['email_to'].attrib['value'] = env_sudo.company.email or ''
        has_cc = {'email_cc', 'email_bcc'} & form_values.keys()
        value = form_values['email_to'].attrib['value'] + (':email_cc' if has_cc else '')
        hash_value = hmac(env_sudo, 'website_form_signature', value)
        if has_cc:
            hash_value += ':email_cc'
        hash_node = etree.Element('input', attrib={'type': "hidden", 'value': hash_value, 'class': "form-control s_website_form_input s_website_form_custom", 'name': "website_form_signature"})
        form_values['email_to'].addnext(hash_node)
