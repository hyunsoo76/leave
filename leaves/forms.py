from django import forms
from .models import LeaveRequest
from .models import Employee
from django.core.validators import MinValueValidator, MaxValueValidator
from .models import CalendarMemo

class LeaveRequestCreateForm(forms.Form):
    # ✅ birth로 직원 자동매칭하지만, birth가 중복이면 직원 선택 필요
    employee_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    leave_type = forms.ChoiceField(
        choices=LeaveRequest.LeaveType.choices,
        label="휴무 종류",
        widget=forms.Select(attrs={"id": "leaveType"}),
    )

    # 반차일 때만 사용
    half_day = forms.ChoiceField(
        choices=LeaveRequest.HalfDay.choices,
        label="반차 구분",
        required=False,
        widget=forms.Select(attrs={"id": "halfDay"}),
    )

    start_date = forms.DateField(
        label="시작일",
        widget=forms.DateInput(attrs={"type": "date", "id": "startDate"}),
    )

    # 연차일 때만 사용(반차면 숨기고 서버에서도 무시)
    end_date = forms.DateField(
        label="종료일",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "id": "endDate"}),
    )

    reason = forms.CharField(
        label="사유",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "선택 입력"}),
    )

    def __init__(self, *args, **kwargs):
        # view에서 employees(동일 birth 후보)를 넘겨주면, 선택 셀렉트를 동적으로 만들기 위함
        employees = kwargs.pop("employees", None)  # [(id, name), ...]
        super().__init__(*args, **kwargs)

        if employees and len(employees) > 1:
            self.fields["employee_choice"] = forms.ChoiceField(
                choices=[(str(eid), name) for eid, name in employees],
                label="직원 선택",
                widget=forms.Select(attrs={"id": "employeeChoice"}),
            )

    def clean(self):
        cleaned = super().clean()
        leave_type = cleaned.get("leave_type")
        start = cleaned.get("start_date")
        end = cleaned.get("end_date") or start
        half_day = cleaned.get("half_day")

        if not start:
            return cleaned

        if end and end < start:
            self.add_error("end_date", "종료일은 시작일보다 빠를 수 없습니다.")

        # 반차: 하루만 + 오전/오후 필수
        if leave_type == LeaveRequest.LeaveType.HALF:
            cleaned["end_date"] = start  # ✅ 반차면 종료일은 시작일로 강제
            if not half_day:
                self.add_error("half_day", "반차는 오전/오후를 선택해주세요.")
        else:
            # 연차면 half_day 비움
            cleaned["half_day"] = None

        return cleaned
    

class CompGrantBulkForm(forms.Form):
    employees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(is_active=True).order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        label="직원 선택",
    )
    worked_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="근무한 날짜(공휴일/주말)",
    )
    holiday_name = forms.CharField(
        required=False,
        max_length=100,
        label="공휴일명(예: 어린이날)",
    )
    amount = forms.DecimalField(
        max_digits=4,
        decimal_places=1,
        initial=1.0,
        label="발생 대체휴무(0.5/1/...)",
    )
    memo = forms.CharField(
        required=False,
        max_length=200,
        label="메모",
    )
    
class CalendarMemoForm(forms.ModelForm):
    class Meta:
        model = CalendarMemo
        fields = ["memo_date", "title", "content", "color"]
        widgets = {
            "memo_date": forms.DateInput(attrs={"type": "date"}),
            "title": forms.TextInput(attrs={"placeholder": "예: 공휴일 근무 / 회식 / 점검"}),
            "content": forms.TextInput(attrs={"placeholder": "메모 내용을 입력"}),
        }
