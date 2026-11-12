#!/usr/bin/env python3
"""End-to-end smoke test: submit real fits to a running zunzunsite3 server and check the results.

Usage:
    python3 deploy/smoke_test.py [BASE_URL] [SCENARIO ...]

BASE_URL defaults to http://127.0.0.1:8765 (a test server, see deploy/test_server.sh).
Run against the production port only if you accept a few fits' worth of CPU load.

Each scenario fetches an interface page, submits its form with the default values
(plus scenario overrides), polls /StatusAndResults/ until the fit finishes, then checks
the results page for expected text and for known failure markers. Exit status is the
number of failed scenarios.
"""
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

# "n/a" in the data statistics is how the removed scipy.mean etc. used to show up
FAILURE_MARKERS = ['Exception creating', 'exception has occurred', 'Traceback', 'see log file']

# 2D data with x > 0 and y > 0 so every fitting target and log scaling works
DATA_2D = '\n'.join('%g %g' % (x, 1.5 + 2.3 * x + 0.4 * x * x + (0.05 if i % 2 else -0.05))
                    for i, x in enumerate([0.5 + 0.37 * k for k in range(20)]))
DATA_3D = '\n'.join('%g %g %g' % (x, y, 1.0 + 0.5 * x + 0.25 * y + 0.01 * ((x * 7 + y * 3) % 5))
                    for x in [1, 2, 3, 4, 5] for y in [1, 2, 3, 4, 5])

LINEAR_2D = 'Equation/2/Polynomial/1st%20Order%20%28Linear%29/'

SCENARIOS = {
    # name: (interface path, field overrides, required results-page text)
    'fit2d': (LINEAR_2D, {'textDataEditor': DATA_2D}, ['Coefficient', 'Mean']),
    'fit2d_odr': (LINEAR_2D, {'textDataEditor': DATA_2D, 'fittingTarget': 'ODR'}, ['Coefficient']),
    'fit3d': ('Equation/3/Polynomial/Linear/', {'textDataEditor': DATA_3D, 'animationSize': '0x0'}, ['Coefficient']),
    'fit3d_anim': ('Equation/3/Polynomial/Linear/', {'textDataEditor': DATA_3D, 'animationSize': '320x240'}, ['Coefficient', '.gif']),
    'spline2d': ('Equation/2/Spline/Spline/', {'textDataEditor': DATA_2D}, ['Coefficient']),
    'udf2d': ('Equation/2/UserDefinedFunction/UserDefinedFunction/',
              {'textDataEditor': DATA_2D, 'udfEditor': 'a + b*X + c*X*X'}, ['Coefficient']),
    'udf3d': ('Equation/3/UserDefinedFunction/UserDefinedFunction/',
              {'textDataEditor': DATA_3D, 'udfEditor': 'a + b*X + c*Y', 'animationSize': '0x0'}, ['Coefficient']),
    'characterize2d': ('CharacterizeData/2/', {'textDataEditor': DATA_2D}, ['Mean']),
    'distributions': ('StatisticalDistributions/1/', {'textDataEditor': '\n'.join(str(1.0 + (k * 37 % 23) / 7.0) for k in range(40))}, ['Distribution']),
    'functionfinder2d': ('FunctionFinder__F__/2/', {'textDataEditor': DATA_2D}, ['Go to this equation']),
    'functionfinder3d': ('FunctionFinder__F__/3/', {'textDataEditor': DATA_3D, 'smoothnessControl3D': '3'}, ['Go to this equation']),
}

# a UDF that must be rejected at form validation, never evaluated
UDF_ATTACKS = [
    "__import__('os').getpid()*X",
    "().__class__.__bases__[0]*X",
    "eval('1')*X",
    "open('/etc/passwd')*X",
]


def form_fields(html, overrides):
    soup = BeautifulSoup(html, 'lxml')
    form = None
    for f in soup.find_all('form'):
        if f.find('textarea', attrs={'name': 'textDataEditor'}) is not None:
            form = f
            break
    if form is None:
        raise AssertionError('no data entry form on interface page: ' + html[:300])
    fields = {}
    for el in form.find_all(['input', 'select', 'textarea']):
        name = el.get('name')
        if not name:
            continue
        if el.name == 'textarea':
            fields[name] = el.text
        elif el.name == 'select':
            opt = el.find('option', selected=True) or el.find('option')
            fields[name] = opt.get('value', opt.text) if opt else ''
        elif el.get('type', '').lower() in ('radio', 'checkbox'):
            if el.has_attr('checked'):
                fields[name] = el.get('value', 'on')
        elif el.get('type', '').lower() not in ('submit', 'button'):
            fields[name] = el.get('value', '')
    fields.update(overrides)
    return form.get('action'), fields


def artifact_bytes(session, base, url):
    # /temp/ is served by nginx in production; a bare test server does not serve it,
    # so fall back to reading the file from this checkout's temp directory
    r = session.get(base + url, timeout=60)
    if r.status_code == 200 and r.content:
        return r.content
    if url.startswith('/temp/'):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'temp', url[len('/temp/'):])
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                return f.read()
    return b''


def wait_for_results(session, base, first_response, timeout):
    response = first_response
    deadline = time.time() + timeout
    while 'HTTP-EQUIV=REFRESH' in response.text.upper().replace('"', '').replace("'", ''):
        if time.time() > deadline:
            raise AssertionError('timed out; last status: ' + BeautifulSoup(response.text, 'lxml').get_text()[:300])
        time.sleep(2)
        response = session.get(base + '/StatusAndResults/', timeout=60)
    return response


def run_scenario(base, name, timeout):
    path, overrides, required = SCENARIOS[name]
    session = requests.Session()
    session.get(base + '/', timeout=60)
    page = session.get(base + '/' + path, timeout=60)
    action, fields = form_fields(page.text, overrides)
    response = session.post(base + action, data=fields, timeout=120)
    response = wait_for_results(session, base, response, timeout)
    text = response.text
    for marker in FAILURE_MARKERS:
        if marker in text:
            raise AssertionError('results contain %r' % marker)
    for want in required:
        if want not in text:
            raise AssertionError('results lack %r; page starts: %s' % (want, BeautifulSoup(text, 'lxml').get_text()[:400]))
    if 'Mean' in required and re.search(r'Mean\W+n/a', BeautifulSoup(text, 'lxml').get_text()):
        raise AssertionError('data statistics show Mean n/a')
    pdf = re.search(r'href=["\']?([^"\'> ]+\.pdf)', text, re.I)
    if name.startswith(('fit', 'udf', 'spline')):
        if not pdf:
            raise AssertionError('no PDF link')
        if not artifact_bytes(session, base, pdf.group(1)).startswith(b'%PDF'):
            raise AssertionError('PDF %s is missing or not a PDF' % pdf.group(1))
    if name == 'fit3d_anim':
        gif = re.search(r'(?:href|src)=["\']?([^"\'> ]+\.gif)', text, re.I)
        if not gif or not artifact_bytes(session, base, gif.group(1)).startswith(b'GIF8'):
            raise AssertionError('animation GIF missing or not a GIF')
    if name == 'fit2d':
        r = session.post(base + '/EvaluateAtAPoint/', data={'x': '2.0'}, timeout=60)
        if 'evaluates to' not in r.text:
            raise AssertionError('evaluate at a point: ' + r.text[:300])
    if name.startswith('functionfinder'):
        # the results list has its own PDF of the equations on the page
        if not pdf or not artifact_bytes(session, base, pdf.group(1)).startswith(b'%PDF'):
            raise AssertionError('function finder results page has no PDF')
        # then fit the rank 1 equation on its own, as a user following "Go to this equation"
        link = re.search(r'href=["\']?(/Equation/[23]/[^"\'> ]*RANK=1&[^"\'> ]*)', text, re.I)
        if not link:
            raise AssertionError('no "Go to this equation" link for rank 1')
        page = session.get(base + link.group(1), timeout=120)
        action, fields = form_fields(page.text, {'animationSize': '0x0'} if '/3/' in link.group(1) else {})
        r = wait_for_results(session, base, session.post(base + action, data=fields, timeout=120), timeout)
        if 'Coefficient' not in r.text:
            raise AssertionError('rank 1 equation fit has no coefficients: ' + BeautifulSoup(r.text, 'lxml').get_text()[:300])
        pdf = re.search(r'href=["\']?([^"\'> ]+\.pdf)', r.text, re.I)
        if not pdf or not artifact_bytes(session, base, pdf.group(1)).startswith(b'%PDF'):
            raise AssertionError('rank 1 equation fit has no PDF')


def run_udf_attacks(base):
    path = SCENARIOS['udf2d'][0]
    for attack in UDF_ATTACKS:
        session = requests.Session()
        session.get(base + '/', timeout=60)
        page = session.get(base + '/' + path, timeout=60)
        action, fields = form_fields(page.text, {'textDataEditor': DATA_2D, 'udfEditor': attack})
        response = session.post(base + action, data=fields, timeout=120)
        if 'StatusAndResults' in response.url or 'REFRESH' in response.text.upper():
            raise AssertionError('unsafe UDF was accepted: %r' % attack)


def main():
    args = sys.argv[1:]
    base = 'http://127.0.0.1:8765'
    if args and args[0].startswith('http'):
        base = args.pop(0).rstrip('/')
    names = args or list(SCENARIOS) + ['udf_attacks']
    failures = 0
    for name in names:
        start = time.time()
        try:
            if name == 'udf_attacks':
                run_udf_attacks(base)
            else:
                run_scenario(base, name, timeout=600)
            print('PASS %-18s %5.1fs' % (name, time.time() - start), flush=True)
        except Exception as e:
            failures += 1
            print('FAIL %-18s %5.1fs  %s' % (name, time.time() - start, e), flush=True)
    return failures


if __name__ == '__main__':
    sys.exit(main())
