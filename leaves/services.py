from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from decimal import ROUND_FLOOR
from django.db import models
from decimal import Decimal

@dataclass
class LeaveDemand:
    total: Decimal       # 총 사용량 (연차/반차 포함)
    annual_need: Decimal # 연차에서 차감해야 할 양(초기값)
    comp_need: Decimal   # 대체휴무에서 차감할 양(초기값)

def calc_requested_amount(leave_type: str, start: date, end: date, half_day: str | None) -> Decimal:
    """
    - 연차: start~end inclusive 일수(토/일은 아직 규칙 반영 X, 추후 가능)
    - 반차: 0.5
    """
    if leave_type == "HALF":
        return Decimal("0.5")
    # ANNUAL
    days = (end - start).days + 1
    return Decimal(days)

def split_demand_by_rule(total: Decimal, leave_type: str) -> LeaveDemand:
    """
    규칙:
    - 반차(0.5)는 대체휴무로 차감하지 않는다 -> 전부 연차 차감
    - 연차(정수일수)는 대체휴무를 먼저 차감할 수 있다
    """
    if leave_type == "HALF":
        return LeaveDemand(total=total, annual_need=total, comp_need=Decimal("0"))
    return LeaveDemand(total=total, annual_need=total, comp_need=total)


def floor_to_int_days(x: Decimal) -> Decimal:
    # 1.7 -> 1.0 / 0.9 -> 0.0
    if x <= 0:
        return Decimal("0")
    return x.quantize(Decimal("1"), rounding=ROUND_FLOOR)

def calc_comp_balance(leave_year) -> Decimal:
    """
    leave_year 기준:
    - 발생: CompDayGrant.amount 합
    - 사용: LeaveRequest.used_comp 합
    """
    from .models import CompDayGrant, LeaveRequest  # 지연 import
    granted = CompDayGrant.objects.filter(leave_year=leave_year).aggregate(
        s=models.Sum("amount")
    )["s"] or Decimal("0")
    used = LeaveRequest.objects.filter(leave_year=leave_year).aggregate(
        s=models.Sum("used_comp")
    )["s"] or Decimal("0")
    return Decimal(granted) - Decimal(used)

def auto_deduct(leave_year, leave_type: str, requested_amount: Decimal) -> tuple[Decimal, Decimal]:
    """
    return: (used_comp, used_annual)

    규칙:
    - 대체휴무를 먼저 소진
    - 단, 반차(0.5)는 대체휴무 차감 금지 -> 연차에서만 차감
    - 대체휴무 차감은 '정수(1일 단위)'만 가능
    - 부족해도 저장 가능(연차 잔여 음수 허용)
    """
    # 반차는 무조건 연차
    if leave_type == "HALF":
        return Decimal("0"), requested_amount

    # 연차 신청(정수일수)
    comp_balance = calc_comp_balance(leave_year)          # 예: 1.7 가능
    comp_usable = floor_to_int_days(comp_balance)         # 정수만 사용: 1.0
    req_int = floor_to_int_days(requested_amount)         # 연차는 보통 정수지만 방어코드

    used_comp = min(comp_usable, req_int)
    used_annual = requested_amount - used_comp

    # used_annual은 부족해도 그대로 (음수 잔여 허용은 잔여 계산에서 처리)
    return used_comp, used_annual

