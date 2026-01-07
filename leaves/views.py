from datetime import timedelta, date as dt_date

from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.db.models import Sum, Count

from .models import Employee, LeaveYear, LeaveRequest, CompDayGrant
from .forms import LeaveRequestCreateForm
from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone

from collections import defaultdict
from decimal import Decimal

from .models import LeaveRequest, CompDayGrant, LeaveYear
from django.views.decorators.http import require_http_methods

from django.db import transaction
from .forms import CompGrantBulkForm

from .models import CalendarMemo
from .forms import CalendarMemoForm
from django.utils.dateparse import parse_datetime

import holidays
from datetime import date

from .utils.telegram import send_telegram
from django.conf import settings

def calendar_view(request):
    return render(request, "leaves/calendar.html")


def events_api(request):
    qs = LeaveRequest.objects.select_related("employee").all()

    # ✅ FullCalendar가 넘기는 기간 (있으면 그 기간에 맞춰서만 반환)
    start_str = request.GET.get("start")
    end_str = request.GET.get("end")
    start_dt = parse_datetime(start_str) if start_str else None
    end_dt = parse_datetime(end_str) if end_str else None

    events = []
    for r in qs:
        start = r.start_date
        end = r.end_date or r.start_date
        end_exclusive = end + timedelta(days=1)

        half_label = ""
        if r.leave_type == LeaveRequest.LeaveType.HALF:
            if r.half_day == LeaveRequest.HalfDay.AM:
                half_label = "오전"
            elif r.half_day == LeaveRequest.HalfDay.PM:
                half_label = "오후"

        events.append({
            "id": r.id,
            "title": r.employee.name,   # ✅ 제목은 이름만
            "start": start.isoformat(),
            "end": end_exclusive.isoformat(),
            "allDay": True,
            "extendedProps": {
                "halfLabel": half_label,   # ✅ 반차 정보는 여기
            }
         })
    # ✅ FullCalendar가 start/end 쿼리를 주면 그 범위만 메모 조회(성능 + 정확)
    start_q = request.GET.get("start")
    end_q = request.GET.get("end")

    memo_qs = CalendarMemo.objects.all()
    if start_q and end_q:
        start_dt = parse_datetime(start_q)
        end_dt = parse_datetime(end_q)
        if start_dt and end_dt:
            memo_qs = memo_qs.filter(memo_date__gte=start_dt.date(), memo_date__lt=end_dt.date())

    for m in memo_qs:
        # allDay 이벤트는 end를 다음날로
        events.append({
            "id": f"memo-{m.id}",
            "title": f"📝 {m.title}: {m.content}",
            "start": m.memo_date.isoformat(),
            "end": (m.memo_date + timedelta(days=1)).isoformat(),
            "allDay": True,
            "classNames": ["fc-memo-event"],
            "editable": False,
        })

    ## ===== ✅ 대한민국 공휴일 이벤트 추가 =====
    KR_HOLIDAY_KO = {
        "New Year's Day": "신정",
        "Korean New Year": "설날",
        "The day preceding Korean New Year": "설날 연휴",
        "The second day of Korean New Year": "설날 연휴",
        "Independence Movement Day": "삼일절",
        "Children's Day": "어린이날",
        "Buddha's Birthday": "부처님오신날",
        "Memorial Day": "현충일",
        "Liberation Day": "광복절",
        "Chuseok": "추석",
        "The day preceding Chuseok": "추석 연휴",
        "The second day of Chuseok": "추석 연휴",
        "National Foundation Day": "개천절",
        "Hangul Day": "한글날",
        "Christmas Day": "성탄절",
        "Alternative holiday": "대체공휴일",
        "Local Election Day": "지방선거일",
        "Election Day": "선거일",  # 라이브러리에서 나오는 경우 대비
    }

    def to_ko_holiday_name(en: str) -> str:
        if not en:
            return ""
        s = str(en).strip()

        # 대체공휴일 같이 "Alternative holiday for X" 형태가 나올 수 있어 처리
        if s.lower().startswith("alternative holiday"):
            # "Alternative holiday for Chuseok" -> "대체공휴일(추석)"
            if " for " in s:
                base = s.split(" for ", 1)[1].strip()
                base_ko = KR_HOLIDAY_KO.get(base, base)
                return f"대체공휴일({base_ko})"
            return "대체공휴일"

        return KR_HOLIDAY_KO.get(s, s)  # 매핑 없으면 원문 유지


    if start_dt and end_dt:
        start_d = start_dt.date()
        end_d = end_dt.date()
        years = range(start_d.year, end_d.year + 1)

        kr_holidays = holidays.KR(years=years)
        for hday, name in kr_holidays.items():
            if start_d <= hday < end_d:
                events.append({
                    "id": f"holiday-{hday.isoformat()}",
                    "title": to_ko_holiday_name(name),   # ✅ 여기서 한글로 변환
                    "start": hday.isoformat(),
                    "end": (hday + timedelta(days=1)).isoformat(),
                    "allDay": True,
                    "classNames": ["fc-holiday-event"],
                })
    return JsonResponse(events, safe=False) 



def _count_weekdays(start: dt_date, end: dt_date) -> int:
    if end < start:
        start, end = end, start

    days = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 월~금
            days += 1
        cur += timedelta(days=1)
    return days


def _calc_units(leave_type: str, start: dt_date, end: dt_date) -> float:
    if leave_type == LeaveRequest.LeaveType.HALF:
        return 0.5
    return float(_count_weekdays(start, end))


def _available_comp(leave_year: LeaveYear) -> float:
    granted = CompDayGrant.objects.filter(leave_year=leave_year).aggregate(s=Sum("amount"))["s"] or 0
    used = LeaveRequest.objects.filter(leave_year=leave_year).aggregate(s=Sum("used_comp"))["s"] or 0
    return float(granted) - float(used)


def request_new(request):
    """
    GET: ?date=YYYY-MM-DD&birth=760910
      - birth로 직원 후보 검색
      - 후보 1명 => 자동
      - 후보 2명 이상 => request_new에서 직원 선택만 추가로 받음
    POST:
      - birth hidden으로 재검증
      - 직원 선택이 있는 경우 선택값 검증
      - 저장 + 자동차감(대체휴무 우선, 단 반차는 대체휴무 사용 안함)
    """
    date_str = request.GET.get("date")
    birth = (request.GET.get("birth") or "").strip()
    selected_date = parse_date(date_str) if date_str else None

    # ✅ 후보 직원(동일 birth 가능)
    candidates_qs = Employee.objects.filter(birth_yyMMdd=birth, is_active=True).order_by("name")
    candidates = list(candidates_qs)

    if not candidates:
        messages.error(request, "인증에 실패했습니다. 생년월일 6자리를 다시 확인해주세요.")
        return redirect("leaves:calendar")

    # 화면용 후보 목록
    employee_choices = [(e.id, e.name) for e in candidates]

    if request.method == "POST":
        # 조작 방지: birth는 POST hidden으로 받지만, GET birth와 일치해야 통과
        post_birth = (request.POST.get("birth") or "").strip()
        if post_birth != birth:
            return HttpResponseForbidden("인증 정보가 올바르지 않습니다.")

        form = LeaveRequestCreateForm(request.POST, employees=employee_choices)

        if form.is_valid():
            # ✅ 어떤 직원인가 결정
            employee = None

            # 후보 1명이면 자동
            if len(candidates) == 1:
                employee = candidates[0]
            else:
                # 후보가 여러명이면 선택 필수
                chosen = request.POST.get("employee_choice")
                if not chosen:
                    form.add_error("employee_choice", "직원을 선택해주세요.")
                else:
                    try:
                        chosen_id = int(chosen)
                    except ValueError:
                        chosen_id = None

                    employee = next((e for e in candidates if e.id == chosen_id), None)
                    if not employee:
                        form.add_error("employee_choice", "직원 선택이 올바르지 않습니다.")

            if not employee:
                # employee 결정 실패 -> 폼 다시 렌더
                return render(
                    request,
                    "leaves/request_new.html",
                    {"employee": None, "candidates": employee_choices, "birth": birth, "selected_date": selected_date, "form": form},
                )

            leave_type = form.cleaned_data["leave_type"]
            half_day = form.cleaned_data.get("half_day")
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data.get("end_date") or start

            # LeaveYear(년도계정)
            ly, _ = LeaveYear.objects.get_or_create(
                employee=employee,
                year=start.year,
                defaults={"base_days": 0, "carry_over": 0},
            )

            units = _calc_units(leave_type, start, end)

            # ✅ 대체휴무 우선 소진
            # - 단, 반차(0.5)는 대체휴무 사용하지 않고 연차에서 차감(요구사항)
            used_comp = 0.0
            if leave_type == LeaveRequest.LeaveType.ANNUAL and units >= 1.0:
                comp_avail = _available_comp(ly)
                used_comp = min(comp_avail, units)

            used_annual = max(0.0, units - used_comp)

            LeaveRequest.objects.create(
                leave_year=ly,
                employee=employee,
                leave_type=leave_type,
                half_day=half_day if leave_type == LeaveRequest.LeaveType.HALF else None,
                start_date=start,
                end_date=end,
                reason=form.cleaned_data.get("reason", ""),
                used_comp=used_comp,
                used_annual=used_annual,
            )
            reason = (form.cleaned_data.get("reason") or "").strip()
            # create() 성공 직후 (텔레그램 메시지)
            calendar_url = request.build_absolute_uri(reverse("leaves:calendar"))

            is_half = (leave_type == LeaveRequest.LeaveType.HALF)

            # half_day 라벨(오전/오후) 만들기
            half_label = ""
            if is_half:
                if half_day == LeaveRequest.HalfDay.AM:
                    half_label = "오전"
                elif half_day == LeaveRequest.HalfDay.PM:
                    half_label = "오후"
                else:
                    half_label = ""  # 혹시 비어있을 때 대비

            # ✅ 신청 문구 포맷 통일
            if is_half:
                # 예) 신청일: 2026-01-23 반차(오후)
                apply_line = f"- 신청일: {start} 반차({half_label})" if half_label else f"- 신청일: {start} 반차"
            else:
                # 예) 신청일: 2026-01-13 ~ 2026-01-13 연차
                # (연차는 기간이 하루든 여러날이든 동일 포맷)
                apply_line = f"- 신청일: {start} ~ {end} 연차"

            reason_line = f"- 사유: {reason}\n" if reason else ""

            msg = (
                "연차 신청\n"
                f"- 신청자: {employee.name}\n"
                f"{apply_line}\n"
                f"{reason_line}"
                f"- 달력: {calendar_url}"
            )
            send_telegram(msg)


            messages.success(request, "휴무 신청이 완료되었습니다.")
            return redirect("leaves:calendar")

    else:
        initial = {}
        if selected_date:
            initial = {"start_date": selected_date, "end_date": selected_date}

        form = LeaveRequestCreateForm(initial=initial, employees=employee_choices)

    # 후보가 1명일 때 화면에 보여줄 employee
    employee_single = candidates[0] if len(candidates) == 1 else None

    return render(
        request,
        "leaves/request_new.html",
        {
            "employee": employee_single,
            "candidates": employee_choices,  # 여러명일 때 선택 표시용
            "birth": birth,
            "selected_date": selected_date,
            "form": form,
        },
    )

@staff_member_required
def admin_summary(request, year: int):
    # 직원 목록(활성)
    employees = Employee.objects.filter(is_active=True).order_by("name")

    rows = []
    for emp in employees:
        ly, _ = LeaveYear.objects.get_or_create(
            employee=emp,
            year=year,
            defaults={"base_days": 0, "carry_over": 0},
        )

        base = float(ly.base_days)
        carry = float(ly.carry_over)

        comp_granted = CompDayGrant.objects.filter(leave_year=ly).aggregate(s=Sum("amount"))["s"] or 0
        comp_granted = float(comp_granted)

        # 사용합(스냅샷)
        used_comp = LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_comp"))["s"] or 0
        used_annual = LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_annual"))["s"] or 0
        used_comp = float(used_comp)
        used_annual = float(used_annual)

        total_entitled = base + carry + comp_granted
        total_used = used_comp + used_annual
        remaining = total_entitled - total_used  # 마이너스 가능(요구사항)

        # 대체휴무 발생 내역(어떤 공휴일인지)
        comp_items = list(
            CompDayGrant.objects.filter(leave_year=ly)
            .order_by("worked_date")
            .values("worked_date", "holiday_name", "amount")
        )

        rows.append({
            "emp": emp,
            "ly": ly,
            "base": base,
            "carry": carry,
            "comp_granted": comp_granted,
            "used_comp": used_comp,
            "used_annual": used_annual,
            "total_entitled": total_entitled,
            "total_used": total_used,
            "remaining": remaining,
            "comp_items": comp_items,
        })

    return render(request, "leaves/admin_summary.html", {"year": year, "rows": rows})


@staff_member_required
def employee_detail(request, employee_id: int, year: int):
    emp = get_object_or_404(Employee, id=employee_id)
    ly, _ = LeaveYear.objects.get_or_create(
        employee=emp,
        year=year,
        defaults={"base_days": 0, "carry_over": 0},
    )

    comp_grants = CompDayGrant.objects.filter(leave_year=ly).order_by("worked_date")
    requests = LeaveRequest.objects.filter(leave_year=ly).order_by("start_date", "id")

    base = float(ly.base_days)
    carry = float(ly.carry_over)
    comp_granted = float(comp_grants.aggregate(s=Sum("amount"))["s"] or 0)

    used_comp = float(requests.aggregate(s=Sum("used_comp"))["s"] or 0)
    used_annual = float(requests.aggregate(s=Sum("used_annual"))["s"] or 0)

    total_entitled = base + carry + comp_granted
    total_used = used_comp + used_annual
    remaining = total_entitled - total_used

    return render(
        request,
        "leaves/employee_detail.html",
        {
            "year": year,
            "emp": emp,
            "ly": ly,
            "comp_grants": comp_grants,
            "requests": requests,
            "base": base,
            "carry": carry,
            "comp_granted": comp_granted,
            "used_comp": used_comp,
            "used_annual": used_annual,
            "total_entitled": total_entitled,
            "total_used": total_used,
            "remaining": remaining,
        },
    )


@staff_member_required
def comp_grant_new(request, employee_id: int, year: int):
    emp = get_object_or_404(Employee, id=employee_id)
    ly, _ = LeaveYear.objects.get_or_create(
        employee=emp,
        year=year,
        defaults={"base_days": 0, "carry_over": 0},
    )

    if request.method == "POST":
        worked_date = parse_date(request.POST.get("worked_date", ""))
        holiday_name = (request.POST.get("holiday_name") or "").strip()
        amount = request.POST.get("amount", "").strip()

        # 최소 검증
        if not worked_date:
            messages.error(request, "근무 날짜를 입력해주세요.")
        else:
            try:
                amount_f = float(amount)
            except:
                amount_f = None

            if amount_f not in (0.5, 1.0, 1.5, 2.0):
                messages.error(request, "발생 수량은 0.5 또는 1.0(필요시 1.5/2.0)만 입력해주세요.")
            else:
                CompDayGrant.objects.create(
                    leave_year=ly,
                    worked_date=worked_date,
                    holiday_name=holiday_name,
                    amount=amount_f,
                )
                messages.success(request, "대체휴무 발생이 등록되었습니다.")
                return redirect("leaves:employee_detail", employee_id=emp.id, year=year)

    return render(request, "leaves/comp_grant_new.html", {"emp": emp, "year": year})

def _sum_decimal(v):
    return Decimal(v or 0)


def _get_year_row(employee: Employee, year: int) -> LeaveYear:
    # 없으면 0으로 생성 (관리자가 나중에 base_days/carry_over 입력)
    ly, _ = LeaveYear.objects.get_or_create(
        employee=employee,
        year=year,
        defaults={"base_days": 0, "carry_over": 0},
    )
    return ly


def _calc_year_summary(ly: LeaveYear):
    """
    잔여 계산 (마이너스 허용)
    - 총부여 = base_days + carry_over + comp_granted
    - 사용 = used_comp + used_annual (LeaveRequest에 스냅샷으로 저장되어 있음)
    - 잔여 = 총부여 - 총사용
    """
    comp_granted = _sum_decimal(
        CompDayGrant.objects.filter(leave_year=ly).aggregate(s=Sum("amount"))["s"]
    )
    used_comp = _sum_decimal(
        LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_comp"))["s"]
    )
    used_annual = _sum_decimal(
        LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_annual"))["s"]
    )

    total_grant = _sum_decimal(ly.base_days) + _sum_decimal(ly.carry_over) + comp_granted
    total_used = used_comp + used_annual
    remain = total_grant - total_used

    return {
        "base_days": _sum_decimal(ly.base_days),
        "carry_over": _sum_decimal(ly.carry_over),
        "comp_granted": comp_granted,
        "used_comp": used_comp,
        "used_annual": used_annual,
        "total_grant": total_grant,
        "total_used": total_used,
        "remain": remain,
    }


def _calc_monthly_used(ly: LeaveYear):
    """
    월별 사용 합계(used_comp + used_annual) 기준
    """
    qs = LeaveRequest.objects.filter(leave_year=ly).order_by("start_date")
    month_sum = defaultdict(Decimal)
    for r in qs:
        key = r.start_date.strftime("%Y-%m")
        month_sum[key] += _sum_decimal(r.used_comp) + _sum_decimal(r.used_annual)
    return dict(month_sum)


@staff_member_required
def admin_employee_list(request):
    """
    관리자 전용 전체 리스트
    - year는 쿼리스트링으로 받음. 없으면 올해.
    """
    from datetime import date
    year = int(request.GET.get("year") or date.today().year)

    employees = Employee.objects.filter(is_active=True).order_by("name")

    rows = []
    for emp in employees:
        ly = _get_year_row(emp, year)
        summary = _calc_year_summary(ly)
        monthly = _calc_monthly_used(ly)

        comp_grants = list(
            CompDayGrant.objects.filter(leave_year=ly).order_by("worked_date").values(
                "worked_date", "holiday_name", "amount", "memo"
            )
        )

        rows.append({
            "employee": emp,
            "year": year,
            "summary": summary,
            "monthly": monthly,  # {"2026-01": 1.0, ...}
            "comp_grants": comp_grants,  # 발생 내역
        })

    return render(request, "leaves/admin_employee_list.html", {"rows": rows, "year": year})


def my_summary(request):
    """
    개인 페이지 진입: birth로 직원 찾고, 동일 birth가 2명 이상이면 선택하게.
    - /me/?birth=760910
    """
    birth = (request.GET.get("birth") or "").strip()
    employees = Employee.objects.filter(is_active=True, birth_yyMMdd=birth).order_by("name") if birth else Employee.objects.none()

    if not birth:
        return render(request, "leaves/my_entry.html", {})  # birth 입력 화면

    if employees.count() == 0:
        return render(request, "leaves/my_entry.html", {"error": "일치하는 직원이 없습니다.", "birth": birth})

    if employees.count() == 1:
        return employee_detail(request, employees.first().id)

    # 동일 birth 여러명 -> 선택 화면
    return render(request, "leaves/my_pick_employee.html", {"birth": birth, "employees": employees})


def employee_detail(request, employee_id: int):
    from datetime import date
    year = int(request.GET.get("year") or date.today().year)

    emp = get_object_or_404(Employee, id=employee_id, is_active=True)
    ly = _get_year_row(emp, year)
    summary = _calc_year_summary(ly)
    monthly = _calc_monthly_used(ly)

    # 대체휴무 발생 내역(어떤 공휴일인지 표시용)
    comp_grants = CompDayGrant.objects.filter(leave_year=ly).order_by("worked_date")

    # 사용 내역(날짜별)
    requests = LeaveRequest.objects.filter(leave_year=ly).order_by("-start_date", "-created_at")

    return render(
        request,
        "leaves/employee_detail.html",
        {
            "employee": emp,
            "year": year,
            "summary": summary,
            "monthly": monthly,
            "comp_grants": comp_grants,
            "requests": requests,
        },
    )

def _leave_year_summary(emp: Employee, year: int):
    """직원 1명 + 특정년도 요약(잔여 계산 포함)"""
    ly, _ = LeaveYear.objects.get_or_create(
        employee=emp,
        year=year,
        defaults={"base_days": 0, "carry_over": 0},
    )

    base = float(ly.base_days)
    carry = float(ly.carry_over)

    comp_granted = float(CompDayGrant.objects.filter(leave_year=ly).aggregate(s=Sum("amount"))["s"] or 0)

    used_comp = float(LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_comp"))["s"] or 0)
    used_annual = float(LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_annual"))["s"] or 0)

    total = base + carry + comp_granted
    used_total = used_comp + used_annual
    remain = total - used_total  # ✅ 마이너스 허용

    # 대체휴무(무슨날인지) 표시용
    comp_labels = list(
        CompDayGrant.objects.filter(leave_year=ly)
        .order_by("worked_date")
        .values("worked_date", "holiday_name", "amount")
    )

    return {
        "leave_year": ly,
        "year": year,
        "base": base,
        "carry": carry,
        "comp_granted": comp_granted,
        "used_comp": used_comp,
        "used_annual": used_annual,
        "used_total": used_total,
        "total": total,
        "remain": remain,
        "comp_labels": comp_labels,
    }


def staff_list(request):
    # ✅ 관리자만
    # if not request.user.is_authenticated or not request.user.is_staff:
    #     return HttpResponseForbidden("관리자만 접근 가능합니다.")

    year = int(request.GET.get("year") or timezone.localdate().year)

    # ✅ 추가 (원하는 범위로 조절 가능)
    year_range = range(
        timezone.localdate().year - 2,
        timezone.localdate().year + 2
    )

    employees = Employee.objects.filter(is_active=True).order_by("name")
    rows = []
    for emp in employees:
        rows.append({
            "employee": emp,
            **_leave_year_summary(emp, year),
        })
    comp_summary = (
        CompDayGrant.objects
        .filter(leave_year__year=year, leave_year__employee__is_active=True)
        .values("holiday_name", "amount")
        .annotate(cnt=Count("leave_year__employee", distinct=True))
        .order_by("-cnt", "holiday_name", "amount")
    )    

    return render(request, "leaves/staff_list.html", {"rows": rows, "year": year, "year_range": year_range, "comp_summary": comp_summary})


def staff_detail(request, employee_id: int):
    # if not request.user.is_authenticated or not request.user.is_staff:
    #     return HttpResponseForbidden("관리자만 접근 가능합니다.")

    year = int(request.GET.get("year") or timezone.localdate().year)

    emp = get_object_or_404(Employee, id=employee_id)
    summary = _leave_year_summary(emp, year)

    # 개인 상세: 사용 내역(월별/일자별로 보여줄 데이터)
    requests = (
        LeaveRequest.objects.filter(leave_year=summary["leave_year"])
        .order_by("-start_date", "-id")
    )

    return render(
        request,
        "leaves/staff_detail.html",
        {"employee": emp, "summary": summary, "requests": requests, "year": year},
    )

def _available_comp(leave_year: LeaveYear) -> float:
    granted = CompDayGrant.objects.filter(leave_year=leave_year).aggregate(s=Sum("amount"))["s"] or 0
    used = LeaveRequest.objects.filter(leave_year=leave_year).aggregate(s=Sum("used_comp"))["s"] or 0
    return float(granted) - float(used)

def _available_annual(leave_year: LeaveYear) -> float:
    total = float(leave_year.base_days) + float(leave_year.carry_over)
    used = LeaveRequest.objects.filter(leave_year=leave_year).aggregate(s=Sum("used_annual"))["s"] or 0
    return total - float(used)

@staff_member_required
def admin_summary(request):
    year = int(request.GET.get("year") or timezone.now().year)

    employees = Employee.objects.filter(is_active=True).order_by("name")

    rows = []
    for emp in employees:
        ly, _ = LeaveYear.objects.get_or_create(employee=emp, year=year, defaults={"base_days": 0, "carry_over": 0})

        comp_grants = list(
            CompDayGrant.objects.filter(leave_year=ly).order_by("worked_date")
            .values("worked_date", "holiday_name", "amount")
        )

        comp_total = sum(float(x["amount"]) for x in comp_grants)

        used_comp = float(
            LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_comp"))["s"] or 0
        )
        used_annual = float(
            LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_annual"))["s"] or 0
        )
        used_total = used_comp + used_annual

        total_annual = float(ly.base_days) + float(ly.carry_over)
        total_grant = total_annual + comp_total
        remain_comp = comp_total - used_comp
        remain_annual = total_annual - used_annual
        remain_total = remain_comp + remain_annual  # “총 잔여”(네가 원한 합계 표시)

        rows.append({
            "emp": emp,
            "leave_year": ly,
            "year": year,
            "base_days": float(ly.base_days),
            "carry_over": float(ly.carry_over),
            "comp_total": comp_total,
            "total_grant": total_grant,
            "comp_grants": comp_grants,         # ✅ 어떤 공휴일인지 표기용
            "used_comp": used_comp,
            "used_annual": used_annual,
            "used_total": used_total,
            "remain_comp": remain_comp,
            "remain_annual": remain_annual,
            "remain_total": remain_total,
        })

    return render(request, "leaves/admin_summary.html", {"rows": rows, "year": year})

@require_http_methods(["GET", "POST"])
def me_lookup(request):
    if request.method == "POST":
        birth = (request.POST.get("birth") or "").strip()
        matches = list(Employee.objects.filter(is_active=True, birth_yyMMdd=birth).order_by("name"))

        if not matches:
            messages.error(request, "해당 생년월일로 등록된 직원이 없습니다.")
            return redirect("leaves:me_lookup")

        # 동명이인/동일 생년월일 케이스: 선택 화면
        if len(matches) > 1:
            return render(request, "leaves/me_choose.html", {"employees": matches, "birth": birth})

        return redirect("leaves:me_detail", employee_id=matches[0].id)

    return render(request, "leaves/me_lookup.html")


def me_detail(request, employee_id: int):
    
    emp = get_object_or_404(Employee, id=employee_id, is_active=True)
    year = int(request.GET.get("year") or timezone.now().year)

    # ✅ 추가: 년도 선택 옵션(예: 현재년도 기준 -3 ~ +1)
    years = list(range(timezone.now().year - 3, timezone.now().year + 2))

    ly, _ = LeaveYear.objects.get_or_create(employee=emp, year=year, defaults={"base_days": 0, "carry_over": 0})

    summary = _calc_year_summary(ly)

    comp_grants = list(
        CompDayGrant.objects.filter(leave_year=ly).order_by("worked_date")
        .values("worked_date", "holiday_name", "amount")
    )

    used_comp = float(LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_comp"))["s"] or 0)
    used_annual = float(LeaveRequest.objects.filter(leave_year=ly).aggregate(s=Sum("used_annual"))["s"] or 0)

    total_annual = float(ly.base_days) + float(ly.carry_over)
    comp_total = sum(float(x["amount"]) for x in comp_grants)
    total_grant = total_annual + comp_total

    remain_comp = comp_total - used_comp
    remain_annual = total_annual - used_annual
    remain_total = remain_comp + remain_annual

    # 월별/일자별 사용 내역
    requests = LeaveRequest.objects.filter(employee=emp, leave_year=ly).order_by("-start_date", "-created_at")

    return render(
        request,
        "leaves/me_detail.html",
        {
            "emp": emp,
            "year": year,
            "years": years,
            "leave_year": ly,
            "comp_grants": comp_grants,
            "total_annual": total_annual,
            "comp_total": comp_total,
            "total_grant": total_annual + comp_total,
            "used_comp": used_comp,
            "used_annual": used_annual,
            "remain_comp": remain_comp,
            "remain_annual": remain_annual,
            "remain_total": remain_total,
            "requests": requests,
            "summary": summary,  
        },
    )

def _sum_qs(qs, field):
    return float(qs.aggregate(s=Sum(field))["s"] or 0)

def _year_summary(ly: LeaveYear):
    # 총 연차 = 기본연차 + 이월
    total_annual = float(ly.base_days) + float(ly.carry_over)

    # 대체휴무 발생 합
    comp_granted = _sum_qs(CompDayGrant.objects.filter(leave_year=ly), "amount")

    # 사용 합(스냅샷 기반)
    used_comp = _sum_qs(LeaveRequest.objects.filter(leave_year=ly), "used_comp")
    used_annual = _sum_qs(LeaveRequest.objects.filter(leave_year=ly), "used_annual")

    # 잔여(요구사항: 마이너스 가능)
    comp_remain = comp_granted - used_comp
    annual_remain = total_annual - used_annual
    total_remain = comp_remain + annual_remain
    total_grant = total_annual + comp_granted
    total_used = used_comp + used_annual

    return {
        "total_annual": total_annual,
        "comp_granted": comp_granted,
        "used_comp": used_comp,
        "used_annual": used_annual,
        "comp_remain": comp_remain,
        "annual_remain": annual_remain,
        "total_remain": total_remain,
        "total_grant": total_grant,
        "total_used": total_used,
    }


def staff_summary(request):
    """
    관리자 전용 전체 리스트 (연도 선택)
    """
    year = int(request.GET.get("year") or dt_date.today().year)

    rows = []
    employees = Employee.objects.filter(is_active=True).order_by("name")
    for emp in employees:
        ly, _ = LeaveYear.objects.get_or_create(
            employee=emp, year=year, defaults={"base_days": 0, "carry_over": 0}
        )
        s = _year_summary(ly)

        # 대체휴무 발생 내역(무슨 공휴일인지 표기용)
        comp_list = list(
            CompDayGrant.objects.filter(leave_year=ly)
            .order_by("worked_date")
            .values("worked_date", "holiday_name", "amount")
        )

        rows.append({
            "employee": emp,
            "leave_year": ly,
            "summary": s,
            "comp_list": comp_list,
        })
        

    return render(request, "leaves/staff_summary.html", {"year": year, "rows": rows})

def my_page(request):
    """
    직원 개인 페이지: ?birth=760910
    - 같은 생년월일이 여러 명이면 선택하게(다음 단계에서 처리 가능)
    """
    birth = (request.GET.get("birth") or "").strip()
    year = int(request.GET.get("year") or dt_date.today().year)

    qs = Employee.objects.filter(is_active=True, birth_yyMMdd=birth).order_by("name")

    if not birth or not qs.exists():
        messages.error(request, "인증 정보가 없거나 올바르지 않습니다.")
        return redirect("leaves:calendar")

    # 동명이인/동일생년월일 케이스: 일단 첫 번째(원하면 다음 단계에서 선택 화면 추가)
    emp = qs.first()

    ly, _ = LeaveYear.objects.get_or_create(
        employee=emp, year=year, defaults={"base_days": 0, "carry_over": 0}
    )
    s = _year_summary(ly)

    # 사용 내역(상세)
    reqs = (
        LeaveRequest.objects.filter(leave_year=ly)
        .order_by("-start_date", "-created_at")
    )

    # 발생 내역(상세)
    comp_list = (
        CompDayGrant.objects.filter(leave_year=ly)
        .order_by("worked_date")
    )

    return render(
        request,
        "leaves/my_page.html",
        {"year": year, "employee": emp, "leave_year": ly, "summary": s, "reqs": reqs, "comp_list": comp_list},
    )

@staff_member_required
def comp_grant_bulk(request, year: int):
    if request.method == "POST":
        form = CompGrantBulkForm(request.POST)
        if form.is_valid():
            employees = form.cleaned_data["employees"]
            worked_date = form.cleaned_data["worked_date"]
            holiday_name = form.cleaned_data["holiday_name"]
            amount = form.cleaned_data["amount"]
            memo = form.cleaned_data["memo"]

            grant_year = worked_date.year  # ✅ 핵심

            for emp in employees:
                ly, _ = LeaveYear.objects.get_or_create(
                    employee=emp,
                    year=grant_year,   # ✅ 여기 변경
                    defaults={"base_days": 0, "carry_over": 0},
                )
                CompDayGrant.objects.create(
                    leave_year=ly,
                    worked_date=worked_date,
                    holiday_name=holiday_name,
                    amount=amount,
                    memo=memo,
                )

            messages.success(request, f"{len(employees)}명에게 대체휴무를 일괄 등록했습니다.")
            return redirect(f"{reverse('leaves:admin_summary')}?year={year}")
    else:
        form = CompGrantBulkForm()

    return render(request, "leaves/comp_grant_bulk.html", {"form": form, "year": year})


def calendar_embed(request):
    return render(request, "leaves/calendar_embed.html")

@staff_member_required
def memo_new(request):
    # /manage/memo/new/?date=YYYY-MM-DD 로 들어오면 날짜 미리 채움
    date_str = request.GET.get("date")
    initial = {}
    if date_str:
        initial["memo_date"] = parse_date(date_str)

    if request.method == "POST":
        form = CalendarMemoForm(request.POST)
        if form.is_valid():
            memo_date = form.cleaned_data["memo_date"]
            title = (form.cleaned_data.get("title") or "").strip()
            content = (form.cleaned_data.get("content") or "").strip()

            # ✅ 같은 날짜+제목 메모가 이미 있으면 새로 만들지 말고 업데이트
            obj, created = CalendarMemo.objects.get_or_create(
                memo_date=memo_date,
                title=title,
                defaults={"content": content},
            )
            if not created:
                obj.content = content
                obj.save(update_fields=["content"])

            messages.success(request, "메모가 저장되었습니다.")
            return redirect("leaves:calendar")
    else:
        form = CalendarMemoForm(initial=initial)

    return render(request, "leaves/memo_form.html", {"form": form, "mode": "new"})



@staff_member_required
def memo_edit(request, memo_id: int):
    memo = get_object_or_404(CalendarMemo, id=memo_id)

    if request.method == "POST":
        form = CalendarMemoForm(request.POST, instance=memo)
        if form.is_valid():
            form.save()
            messages.success(request, "메모가 수정되었습니다.")
            return redirect("leaves:calendar")
    else:
        form = CalendarMemoForm(instance=memo)

    return render(request, "leaves/memo_form.html", {"form": form, "mode": "edit", "memo": memo})