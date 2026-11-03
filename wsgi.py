import os, sys

#if os.path.dirname( os.path.realpath( __file__ ) )  not in sys.path:
#    sys.path.append(os.path.dirname( os.path.realpath( __file__ ) ) )
sys.path.append('/var/www/html/zunzunsite3')
    
os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
#os.environ.setdefault("DJANGO_SETTINGS_MODULE", "projectName.settings")

#import django.core.handlers.wsgi
#application = django.core.handlers.wsgi.WSGIHandler()

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

import settings, urls
