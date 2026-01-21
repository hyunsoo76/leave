# leaves/middleware.py
from django.utils import timezone
from django.db.models import F
from .models import VisitorStat

EXCLUDE_PATH_PREFIXES = (
    "/static/",
    "/admin/",
    "/leave/admin/",
    "/favicon.ico",
    "/leave/manage/",   # ✅ 관리자 화면 제외
    "/leave/embed/",   # iframe 달력
    "/leave/api/", 
    "/leave/static/",
)

class VisitorCountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if not path.startswith(EXCLUDE_PATH_PREFIXES):
            today = timezone.localdate()
            obj, _ = VisitorStat.objects.get_or_create(date=today)
            VisitorStat.objects.filter(pk=obj.pk).update(count=F("count") + 1)

        return self.get_response(request)
