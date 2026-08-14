from django.contrib import admin
from .models import Attendance, Employee, LeaveRequest, Shift

admin.site.register(Employee)
admin.site.register(Attendance)
admin.site.register(LeaveRequest)
admin.site.register(Shift)
