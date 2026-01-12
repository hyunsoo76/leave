from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Employee(models.Model):
    """
    직원 마스터 (관리자만 추가/수정/삭제)
    직원은 로그인 대신 '간단인증(birth_yyMMdd)'로 신청할 수 있게만 사용.
    """
    name = models.CharField(max_length=50, unique=True)
    birth_yyMMdd = models.CharField(max_length=6)  # 예: 760910
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class LeaveYear(models.Model):
    """
    직원별 연차 "년도별 계정"
    - carry_over: 이월(+/- 가능)
    - base_days: 관리자가 입력하는 해당년도 기본 연차
    """
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="years")
    year = models.PositiveIntegerField()

    base_days = models.DecimalField(
        max_digits=4, decimal_places=1, default=0,
        validators=[MinValueValidator(0)]
    )
    carry_over = models.DecimalField(
        max_digits=5, decimal_places=1, default=0
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("employee", "year")
        indexes = [
            models.Index(fields=["year"]),
            models.Index(fields=["employee", "year"]),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.year}"


class CompDayGrant(models.Model):
    """
    대체휴무 발생(공휴일 근무로 발생)
    - 0.5도 가능
    - 무슨 공휴일인지 label로 남김 (예: "05/05 어린이날")
    - 소멸 규칙 없음
    """
    leave_year = models.ForeignKey(LeaveYear, on_delete=models.CASCADE, related_name="comp_grants")

    worked_date = models.DateField()  # 실제 근무한 공휴일 날짜
    holiday_name = models.CharField(max_length=100, blank=True)  # 예: "어린이날"
    amount = models.DecimalField(
        max_digits=4, decimal_places=1,
        validators=[MinValueValidator(0.5), MaxValueValidator(5)]
    )

    memo = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["worked_date"]),
        ]

    def __str__(self):
        label = self.holiday_name or "공휴일근무"
        return f"{self.leave_year.employee.name} {self.worked_date} {label} +{self.amount}"


class LeaveRequest(models.Model):
    """
    휴무 신청
    - 직원이 간단인증 후 신청 가능
    - 수정/삭제는 관리자만 (권한은 뷰에서 강제)
    - 반차: start_date=end_date, half_day 지정
    - 연차(기간): start_date~end_date
    - 차감은 '자동차감'
    """
    class LeaveType(models.TextChoices):
        ANNUAL = "ANNUAL", "연차"
        HALF = "HALF", "반차"

    class HalfDay(models.TextChoices):
        AM = "AM", "오전"
        PM = "PM", "오후"

    leave_year = models.ForeignKey(LeaveYear, on_delete=models.CASCADE, related_name="requests")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="requests")

    leave_type = models.CharField(max_length=10, choices=LeaveType.choices)

    start_date = models.DateField()
    end_date = models.DateField()

    half_day = models.CharField(max_length=2, choices=HalfDay.choices, blank=True, null=True)

    reason = models.CharField(max_length=200, blank=True)

    # 자동차감 결과(스냅샷)
    used_comp = models.DecimalField(max_digits=4, decimal_places=1, default=0)   # 대체휴무로 차감된 양
    used_annual = models.DecimalField(max_digits=4, decimal_places=1, default=0) # 연차로 차감된 양

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["start_date"]),
            models.Index(fields=["end_date"]),
            models.Index(fields=["employee", "start_date"]),
        ]

    def __str__(self):
        if self.leave_type == self.LeaveType.HALF:
            return f"{self.employee.name} {self.start_date} ({self.get_half_day_display()} 반차)"
        if self.start_date == self.end_date:
            return f"{self.employee.name} {self.start_date} 연차"
        return f"{self.employee.name} {self.start_date}~{self.end_date} 연차"

class CompDayUse(models.Model):
    """
    특정 LeaveRequest가 어떤 CompDayGrant(대체휴무 발생분)를 얼마나 썼는지 기록
    => 개인페이지에 '어떤 공휴일 대체휴무를 썼는지' 표시 가능
    """
    leave_request = models.ForeignKey("LeaveRequest", on_delete=models.CASCADE, related_name="comp_uses")
    grant = models.ForeignKey("CompDayGrant", on_delete=models.CASCADE, related_name="uses")

    amount = models.DecimalField(max_digits=4, decimal_places=1, validators=[MinValueValidator(0.5)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["leave_request"]),
            models.Index(fields=["grant"]),
        ]

class CalendarMemo(models.Model):
    memo_date = models.DateField(db_index=True)
    title = models.CharField(max_length=60, default="메모")
    content = models.CharField(max_length=200)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["memo_date"])]

    def __str__(self):
        return f"{self.memo_date} - {self.title}"
    
class CalendarMemo(models.Model):
    memo_date = models.DateField()
    title = models.CharField(max_length=100)
    content = models.TextField(blank=True)

    COLOR_CHOICES = [
        ("green", "녹색"),
        ("blue", "파랑"),
        ("yellow", "노랑"),
        ("red", "빨강"),
        ("gray", "회색"),
    ]

    color = models.CharField(
        max_length=10,
        choices=COLOR_CHOICES,
        default="green"
    )
