from django.contrib import admin
from .models import Employee, LeaveYear, CompDayGrant, LeaveRequest
from .models import CalendarMemo


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "birth_yyMMdd", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "birth_yyMMdd")
    ordering = ("name",)


@admin.register(LeaveYear)
class LeaveYearAdmin(admin.ModelAdmin):
    list_display = ("employee", "year", "base_days", "carry_over", "created_at")
    list_filter = ("year",)
    search_fields = ("employee__name",)
    ordering = ("-year", "employee__name")
    list_editable = ("base_days", "carry_over")  # ✅ 목록에서 바로 수정


@admin.register(CompDayGrant)
class CompDayGrantAdmin(admin.ModelAdmin):
    list_display = ("employee_name", "year", "worked_date", "holiday_name", "amount", "created_at")
    list_filter = ("worked_date", "leave_year__year")
    search_fields = ("leave_year__employee__name", "holiday_name", "memo")
    ordering = ("-worked_date",)

    @admin.display(description="직원", ordering="leave_year__employee__name")
    def employee_name(self, obj):
        return obj.leave_year.employee.name

    @admin.display(description="연도", ordering="leave_year__year")
    def year(self, obj):
        return obj.leave_year.year


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "start_date", "end_date",
        "leave_type", "half_day",
        "used_comp", "used_annual",
        "created_at"
    )
    list_filter = ("leave_type", "half_day", "start_date", "leave_year__year")
    search_fields = ("employee__name", "reason")
    ordering = ("-start_date", "-created_at")

@admin.register(CalendarMemo)
class CalendarMemoAdmin(admin.ModelAdmin):
    list_display = ("memo_date", "title", "content", "updated_at")
    list_filter = ("memo_date",)
    search_fields = ("title", "content")