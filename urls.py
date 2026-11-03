from django.views.decorators.cache import cache_page
from django.urls import re_path as url
import zunzun.views
import settings

urlpatterns = [
    url(r"^$", zunzun.views.HomePageView),
    url(r"^StatusAndResults/", zunzun.views.StatusView),
    url(r"^CharacterizeData/([123])/$", zunzun.views.LongRunningProcessView),
    url(r"^StatisticalDistributions/([1])/$", zunzun.views.LongRunningProcessView),
    url(r"^FunctionFinder__.__/([23])/$", zunzun.views.LongRunningProcessView),
    url(r"^FunctionFinderResults/([23])/$", zunzun.views.LongRunningProcessView),
    url(r"^FitEquation__F__/([23])/(.+)/(.+)/$", zunzun.views.LongRunningProcessView),
    url(r"^Equation/([23])/(.+)/(.+)/$", zunzun.views.LongRunningProcessView),
    url(r"^EvaluateAtAPoint/$", zunzun.views.EvaluateAtAPointView),
    url(r"^AllEquations/([23])/(.+)/$", zunzun.views.AllEquationsView),
    url(r"^Feedback/$", zunzun.views.FeedbackView),
]
