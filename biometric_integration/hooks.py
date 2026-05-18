app_name = "biometric_integration"

# ZKTeco ADMS push protocol — devices POST to /iclock/cdata, /iclock/getrequest etc.
page_renderer = ["biometric_integration.biometric_integration.iclock.IclockRenderer"]
app_title = "Biometric Integration"
app_publisher = "Dikshya"
app_description = "Biometric Integration"
app_email = "dikshya@raindropinc.com "
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "biometric_integration",
# 		"logo": "/assets/biometric_integration/logo.png",
# 		"title": "Biometric Integration",
# 		"route": "/biometric_integration",
# 		"has_permission": "biometric_integration.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/biometric_integration/css/biometric_integration.css"
# app_include_js = "/assets/biometric_integration/js/biometric_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/biometric_integration/css/biometric_integration.css"
# web_include_js = "/assets/biometric_integration/js/biometric_integration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "biometric_integration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "biometric_integration/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "biometric_integration.utils.jinja_methods",
# 	"filters": "biometric_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "biometric_integration.install.before_install"
# after_install = "biometric_integration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "biometric_integration.uninstall.before_uninstall"
# after_uninstall = "biometric_integration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "biometric_integration.utils.before_app_install"
# after_app_install = "biometric_integration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "biometric_integration.utils.before_app_uninstall"
# after_app_uninstall = "biometric_integration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "biometric_integration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

fixtures = ["Custom Field"]


doc_events = {

    "Attendance": {
        "before_save": [
            "biometric_integration.biometric_integration.attendance_override.set_shift_deviation_fields",
        ],
        "before_submit": [
            "biometric_integration.biometric_integration.attendance_override.adjust_out_time"
        ],
        "on_update": [
           "biometric_integration.biometric_integration.attendance_override.auto_submit_attendance"
        ]
        # "before_submit": "biometric_integration.biometric_integration.attendance_override.cap_working_hours_to_shift_end",
        # "on_submit": "biometric_integration.biometric_integration.attendance_override.create_compensatory_leave_on_holiday",
    }
}



# Scheduled Tasks
# ---------------
# Attendance is handled by ERPNext's built-in auto attendance (Shift Type → Enable Auto Attendance)

# Testing
# -------

# before_tests = "biometric_integration.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "biometric_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "biometric_integration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = []
# after_request = ["biometric_integration.utils.after_request"]

# Job Events
# ----------
# before_job = ["biometric_integration.utils.before_job"]
# after_job = ["biometric_integration.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"biometric_integration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
