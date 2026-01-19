# leaves/urls.py
from django.urls import path
from . import views

app_name = "leaves"

urlpatterns = [
    # ===== Public =====
    path("", views.calendar_view, name="calendar"),
    path("embed/calendar/", views.calendar_embed, name="calendar_embed"),
    path("api/events/", views.events_api, name="events_api"),
    path("request/new/", views.request_new, name="request_new"),

    # 개인 페이지 (생년월일 인증 흐름)
    path("me/", views.me_lookup, name="me_lookup"),                 # 생년월일 입력/선택
    path("me/<int:employee_id>/", views.me_detail, name="me_detail"),# 개인 상세

    # 직원 공개 리스트(원하면 유지, 아니면 제거 가능)
    path("staff/", views.staff_list, name="staff_list"),
    path("staff/<int:employee_id>/", views.staff_detail, name="staff_detail"),

    # ===== 관리자 전용(앱 내부 관리 화면) =====
    # ⚠️ Django admin(/admin/)과 혼동 피하려고 manage/로 분리
    path("manage/summary/", views.admin_summary, name="admin_summary"),  # 기본: 올해 요약
    path("manage/summary/<int:year>/", views.admin_summary, name="admin_summary_year"),

    path("manage/employees/", views.admin_employee_list, name="admin_employee_list"),
    path("manage/employee/<int:employee_id>/<int:year>/", views.admin_employee_detail, name="admin_employee_detail"),

    path("manage/comp/new/<int:employee_id>/<int:year>/", views.comp_grant_new, name="comp_grant_new"),
    path("manage/comp/bulk/<int:year>/", views.comp_grant_bulk, name="comp_grant_bulk"),

    path("manage/memo/new/", views.memo_new, name="memo_new"),
    path("manage/memo/<int:memo_id>/edit/", views.memo_edit, name="memo_edit"),
]
