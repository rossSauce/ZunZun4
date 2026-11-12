import os

# the exception logs in TEMP_FILES_DIR use logging.basicConfig(level=DEBUG), which otherwise
# fills them with matplotlib/PIL debug chatter (font matching, update_title_pos, etc.)
import logging
for _noisyLogger in ('matplotlib', 'PIL'):
    logging.getLogger(_noisyLogger).setLevel(logging.WARNING)

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
#BASE_DIR = os.path.dirname(os.path.dirname(__file__))
BASE_DIR = '/var/www/html/zunzunsite3/'



# this is for serving static files with the django development server
import sys
if 'runserver' in sys.argv:
    DEBUG = True
    TEMPLATE_DEBUG = True
else:
    DEBUG = False
    TEMPLATE_DEBUG = False

ADMINS = [('Ross Moffett', 'findcurves@gmail.com')]

EXCEPTION_EMAIL_ADDRESS = 'findcurves@gmail.com' # for unknown site exceptions
FEEDBACK_EMAIL_ADDRESS = 'findcurves@gmail.com' # for any user feedback

SESSION_COOKIE_NAME = 'sessionid'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 5 # 60 seconds * 60 minutes * 24 hours * 5 days

EMAIL_USE_TLS = True # assuming gmail
EMAIL_PORT = 587 # assuming gmail
EMAIL_HOST = 'smtp.gmail.com' # assuming gmail
EMAIL_HOST_USER = 'findcurves@gmail.com'
# The SMTP password is kept outside the repository, like SECRET_KEY below.
# Set ZUNZUN_EMAIL_PASSWORD, or ZUNZUN_EMAIL_PASSWORD_FILE to point at a different file.
# Without it the site still runs; only sending email fails.
EMAIL_HOST_PASSWORD = os.environ.get('ZUNZUN_EMAIL_PASSWORD', '')
if not EMAIL_HOST_PASSWORD:
    try:
        with open(os.environ.get('ZUNZUN_EMAIL_PASSWORD_FILE', '/etc/zunzunsite3/email_password')) as _f:
            EMAIL_HOST_PASSWORD = _f.read().strip()
    except OSError:
        pass

MANAGERS = ADMINS

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # absolute, so a server started from another working directory cannot silently create
        # and use a second, empty database; ZUNZUN_DB_PATH lets a test server use a copy
        'NAME': os.environ.get('ZUNZUN_DB_PATH', os.path.join(BASE_DIR, 'session_db', 'db.sqlite3')),
        'OPTIONS':{'timeout':35} # in case database is busy or slow
    }
}

# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# NOTE:vIf running in a Windows environment this must be set to the
#vsame as your system time zone.
TIME_ZONE = 'America/Chicago'

# Language code for this installation. All choices can be found here:
# http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGE_CODE = 'en-us'

SITE_ID = 1 # we're number one! we're number one!

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = False

# Make this unique, and don't share it with anybody.
# The key is kept outside the repository and outside the nginx web root.
# Set ZUNZUN_SECRET_KEY, or ZUNZUN_SECRET_KEY_FILE to point at a different file.
SECRET_KEY = os.environ.get('ZUNZUN_SECRET_KEY')
if not SECRET_KEY:
    with open(os.environ.get('ZUNZUN_SECRET_KEY_FILE', '/etc/zunzunsite3/secret_key')) as _f:
        SECRET_KEY = _f.read().strip()
if not SECRET_KEY:
    raise RuntimeError('SECRET_KEY file is empty')

# List of callables that know how to import templates from various sources.
# older versions of django use TEMPLATE_*
TEMPLATE_LOADERS = (
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
)

MIDDLEWARE_CLASSES = (
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',

    #'django.contrib.auth.middleware.AuthenticationMiddleware',

)
MIDDLEWARE=MIDDLEWARE_CLASSES

ROOT_URLCONF = 'urls'

#ROOT_PATH = 'os.path.dirname(__file__)'
ROOT_PATH = '/var/www/html/zunzunsite3/'

# older versions of django use TEMPLATE_*
TEMPLATE_DIRS = (
    #os.path.join(ROOT_PATH, 'templates'),
    '/var/www/html/zunzunsite3/templates',
)

# newer versions of django use TEMPLATES below, not the older TEMPLATE_* above
# this file has both for compatibility although this gives a (harmless) warning
# because both coding styles are present in the same settings.py file
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': TEMPLATE_DIRS,
        'APP_DIRS': True,
        'OPTIONS': {
            # ... some options here ...
        },
    },
]
INSTALLED_APPS = (
    'corsheaders',
#    'django.contrib.auth',
#    'django.contrib.contenttypes',
    'django.contrib.sessions',
#    'django.contrib.sites',
    'django.contrib.staticfiles',
    'zunzun',
)

# JSON, not pickle: PickleSerializer was removed in Django 5.0, and unpickling session data
# runs code if SECRET_KEY ever leaks. Every value the site stores in a session is a str or int
# (the status/data stores hold their items as hex strings, see SaveDictionaryOfItemsToSessionStore).
# Sessions saved in the old pickle format read as empty, so a restart that switches format ends
# any fit in progress; users simply start again.
SESSION_SERIALIZER = 'django.contrib.sessions.serializers.JSONSerializer'

STATIC_URL = '/temp/'

#TEMP_FILES_DIR = os.path.join(ROOT_PATH, 'temp')
TEMP_FILES_DIR = '/var/www/html/zunzunsite3/temp'
MAX_TEMP_DIR_SIZE_IN_MBYTES = 500 # default 500 megabytes maximum

# fits one client address may have running at once (each uses every core it can get);
# a new fit from the same browser replaces that browser's previous one, which counts until it stops
MAX_CONCURRENT_FITS_PER_IP = int(os.environ.get('ZUNZUN_MAX_CONCURRENT_FITS_PER_IP', '3'))

# a single parallel work item (one statistical distribution, one report or graph) that takes
# longer than this is abandoned, so one pathological case cannot hang the whole fit
PARALLEL_ITEM_TIMEOUT_SECONDS = int(os.environ.get('ZUNZUN_PARALLEL_ITEM_TIMEOUT_SECONDS', '120'))

STATICFILES_DIRS = (
    TEMP_FILES_DIR,
)


# CORS_ALLOWED_ORIGINS = [
#    'https://www.findcurves.com',
#    'http://www.findcurves.com',
#    'http://localhost',
#    'https://127.0.0.1',
# ]
# ALLOWED_HOSTS=[
#    'https://www.findcurves.com',
#    'http://www.findcurves.com',
#    'http://localhost',
#    'https://127.0.0.1',
# ]#['*']#['127.0.0.1', '34.71.107.249']
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = False
# host names this site answers to; nginx also drops requests for any other name
ALLOWED_HOSTS=['findcurves.com', 'www.findcurves.com', 'localhost', '127.0.0.1']

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]
