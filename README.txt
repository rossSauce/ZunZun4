Welcome to zunzunsite3, a Django site in Python 3 for curve fitting 2D
and 3D data that can output source code in several computing languages
and run a genetic algorithm for initial parameter estimation. Includes
orthogonal distance and relative error regressions. Generates PDF files
and surface animations. Based on code from zunzun.com, written by
James R. Phillips. Now maintained by Ross Moffett: findcurves@gmail.com

You will need to install scipy, matplotlib, pillow, django, bs4,
reportlab and psutil to run this software. (ImageMagick and gifsicle
are no longer needed: animations are written with Pillow.) On
Debian and Ubuntu, you can run the following:

apt-get install python3-scipy python3-matplotlib python3-pil
apt-get install python3-django python3-bs4
apt-get install python3-reportlab python3-requests
apt-get install python3-psutil

then install the pyeq3 fitting code with these commands:

apt-get install python3-pip
pip3 install --user pyeq3

(Newer Debian releases refuse pip installs outside a virtualenv; add
--break-system-packages to the pip3 command if it reports an
"externally-managed-environment" error.) pyeq3 is installed for the
account that runs the site, so run pip3 as that user, not with sudo.


SECRET KEY

The Django secret key is not stored in the repository. settings.py
reads it from /etc/zunzunsite3/secret_key, or from the environment
variable ZUNZUN_SECRET_KEY if that is set (ZUNZUN_SECRET_KEY_FILE
points to a different file). The site will not start without it.
To create the file on a new machine:

sudo install -d -o root -g <USERNAME> -m 750 /etc/zunzunsite3
python3 -c "from django.core.management.utils import get_random_secret_key as g; print(g())" | sudo tee /etc/zunzunsite3/secret_key >/dev/null
sudo chown root:<USERNAME> /etc/zunzunsite3/secret_key
sudo chmod 640 /etc/zunzunsite3/secret_key

Changing the key logs out every visitor (existing sessions stop working).

The SMTP password for site email (EMAIL_HOST_PASSWORD) is kept the
same way, in /etc/zunzunsite3/email_password, or the environment
variable ZUNZUN_EMAIL_PASSWORD (ZUNZUN_EMAIL_PASSWORD_FILE points to a
different file). Without it the site runs but cannot send email.

printf '%s\n' 'APP-PASSWORD-HERE' | sudo tee /etc/zunzunsite3/email_password >/dev/null
sudo chown root:<USERNAME> /etc/zunzunsite3/email_password
sudo chmod 640 /etc/zunzunsite3/email_password

A pre-commit hook in deploy/git-hooks/ refuses commits that contain
passwords or keys. Enable it once per clone with:

git config core.hooksPath deploy/git-hooks


DEVELOPMENT SERVER

cd to the project's top-level directory and run the django
development server with the command:

python3 manage.py runserver 8001

and open the url http://127.0.0.1:8001/ in a browser. Cool!
Port 8000 is used by the production server below. The development
server runs with DEBUG on and reloads when files change.


PRODUCTION SERVER

On the production machine the site runs under gunicorn as the systemd
service zunzunsite3, on 127.0.0.1:8000 behind nginx. The files for
this are in deploy/.

One-time setup (run once per machine, from the project directory):

sudo bash deploy/install.sh

This installs the nginx sites, a swap file and the zunzunsite3
service, backing up anything it replaces, and starts the site. It is
safe to run again.

Starting the site: after that setup the site STARTS AUTOMATICALLY
whenever the server boots, and systemd restarts it within 5 seconds
if it crashes. No cron entry or /etc/rc.local line is needed (the old
rc.local line that ran runZunZun.sh is commented out by install.sh;
don't add a cron @reboot line either, as it would compete with the
service for port 8000). To start, stop or check it by hand:

sudo systemctl start zunzunsite3       start the site
sudo systemctl stop zunzunsite3        stop the site
sudo systemctl restart zunzunsite3     after changing code or settings
                                       (gunicorn does not reload by itself)
sudo systemctl status zunzunsite3      is it running? (and whether it
                                       is enabled to start at boot)
sudo journalctl -u zunzunsite3 -f      follow the server output

If the site does not come back after a reboot, check
"systemctl is-enabled zunzunsite3" says "enabled"; if not, run
"sudo systemctl enable --now zunzunsite3".

nginx serves the generated graphs, result pages and PDFs in temp/
directly; only those file types are public, not the *.log exception
logs or pid_*.trace files that are also written there. Requests for
any host name other than findcurves.com are dropped. After changing
deploy/nginx/*, re-run deploy/install.sh to install them.

Restarting the service does not stop fits that are already running,
because those are forked off into their own processes.


SESSION DATABASE

Sessions are stored in session_db/db.sqlite3, which is not tracked
in git (see .gitignore). Expired sessions are removed a few thousand
at a time whenever the home page is generated. To reclaim disk space
after a large cleanup, see session_db/vacuum.txt.


SECURITY CHECK

deploy/security.py checks the server for signs of compromise: unknown
programs running, programs started from temporary directories or
hidden from the process list, cryptominer and botnet connections,
startup hooks (cron, systemd, shell startup files, ld.so.preload,
Python .pth files, git hooks), changed system and pip package files,
and code dropped into /var/www or temp/. It only reads; it changes
nothing. Run it as root for complete results:

sudo python3 deploy/security.py            full check (slow: 10+ minutes)
sudo python3 deploy/security.py --quick    skips the package and hidden-process checks

Findings are ALERT (investigate now), WARN (review) and INFO (for
context). The exit status is 2 for alerts, 1 for warnings, 0 if
clean, and 3 if emailing the report failed.

Emailing reports: add --email ADDRESS to any run. The SMTP login is
read from /etc/zunzunsite3/security_email.conf (root-owned, mode 600;
deploy/install.sh creates it from deploy/security_email.conf.example).
For Gmail, the password must be a 16-letter app password (Google
account > Security > 2-Step Verification > App passwords), not the
account password. Test it with a quick run:

sudo python3 deploy/security.py --quick --email rossmoffett@findcurves.com

Weekly check: deploy/install.sh installs a cron job
(/etc/cron.d/zunzunsite3-security) that runs the full check every
Sunday at 03:30 and emails rossmoffett@findcurves.com if there are any
warnings or alerts (set SECURITY_EMAIL=... when running install.sh to
use another address). The last report is kept in
/var/log/zunzunsite3-security.log. Cron runs a root-owned copy at
/usr/local/sbin/zunzun-security, so after changing
deploy/security.py, re-run deploy/install.sh to update that copy. A clean report is not proof the
server is clean; if an ALERT turns out to be real, rebuild the server
rather than trying to clean it.


LOG FILES

When something fails, the traceback is logged. Under the production
server (gunicorn) it goes to the service journal:

sudo journalctl -u zunzunsite3 --since today | grep -A40 ERROR

Under the development server (manage.py runserver), and from some
background jobs, it is written to temp/ as <process id>.log instead.
Check both after errors are reported. Failures in individual graphs
or reports are logged but do not stop the rest of the results page.


NOTES: the code uses Unix-style process forking, and this
is not available on the  Windows operating system.

My tests show that while both mod_wsgi and gunicorn work fine
for Django production servers, the uwsgi process model would not
allow os.fork() calls to work as required for this software.


DATA IMPORT

The data editor on every fitting page can import CSV/text files and
Excel workbooks (.xlsx .xlsm .xls .ods), and on 2D pages digitize a
graph image (axis calibration, clicked points, or tracing a curve by
colour). This runs entirely in the browser: files are never uploaded,
the numbers are put into the text editor and validated by the server as
usual. The code is temp/static_images/zunzun_data_import.js (adapted
from tkinterFit). Excel files are read with SheetJS, a pinned copy in
temp/static_images/xlsx-0.20.3.full.min.js (Apache 2.0 licence beside
it), loaded only when a workbook is opened. To update it, download the
current release from https://cdn.sheetjs.com/ (not npm, whose "xlsx"
package is outdated and has known vulnerabilities), replace the file and
change SHEETJS_URL in zunzun_data_import.js. Browser tests:
deploy/test_data_import.html (instructions at its top).


TESTING

deploy/smoke_test.py submits real fits (2D, 3D, ODR, spline, user
defined functions, characterizer, statistical distributions, function
finder, animation) and checks the results pages, PDFs and GIFs. It also
checks that unsafe user defined functions are rejected. Run it against
a throwaway server so production is not loaded or touched:

deploy/test_server.sh /tmp/zunzun-test
python3 deploy/smoke_test.py                 # all scenarios, several minutes
python3 deploy/smoke_test.py fit2d udf2d     # or just some
kill $(cat /tmp/zunzun-test/gunicorn.pid)

Run it after any code change or package upgrade (Debian, pyeq3) and
before restarting the production service.


LIMITS

settings.py has MAX_CONCURRENT_FITS_PER_IP (default 3, or environment
variable ZUNZUN_MAX_CONCURRENT_FITS_PER_IP) and
PARALLEL_ITEM_TIMEOUT_SECONDS (default 120): one statistical
distribution, report or graph that takes longer than that is skipped
so a single pathological case cannot hang a whole fit.
