@tool
extends EditorPlugin
## Agent DevTools. Enabling the plugin registers the DevTools autoload and makes sure the
## project setting devtools/allow_mutation exists (false). Disabling it removes the autoload
## and leaves the setting alone.
##
## The setup runs in _enable_plugin and _disable_plugin, which the editor calls only when the
## plugin is switched on or off. Starting the editor on a project that is already set up
## changes nothing in project.godot.

const AUTOLOAD_NAME: String = "DevTools"
const AUTOLOAD_PATH: String = "res://addons/agent_devtools/dev_tools.gd"
const AUTOLOAD_SETTING: String = "autoload/" + AUTOLOAD_NAME
const ALLOW_MUTATION_SETTING: String = "devtools/allow_mutation"


func _enter_tree() -> void:
	# Shows the setting as a checkbox in Project Settings. Not saved to project.godot.
	if ProjectSettings.has_setting(ALLOW_MUTATION_SETTING):
		_describe_mutation_setting()


func _enable_plugin() -> void:
	var registered: String = _registered_autoload_path()
	if registered.is_empty():
		add_autoload_singleton(AUTOLOAD_NAME, AUTOLOAD_PATH)
	elif registered != AUTOLOAD_PATH:
		var found: String = "the autoload %s points at %s" % [AUTOLOAD_NAME, registered]
		push_warning("Agent DevTools: %s; left as it is. It must be %s." % [found, AUTOLOAD_PATH])
	if not ProjectSettings.has_setting(ALLOW_MUTATION_SETTING):
		# No initial value is declared for it: a setting equal to its initial value is left
		# out of project.godot, and this one should be visible there as an explicit "false".
		ProjectSettings.set_setting(ALLOW_MUTATION_SETTING, false)
		_describe_mutation_setting()


func _disable_plugin() -> void:
	if _registered_autoload_path() == AUTOLOAD_PATH:
		remove_autoload_singleton(AUTOLOAD_NAME)


## The script the DevTools autoload points at, as a res:// path; "" when there is none.
static func _registered_autoload_path() -> String:
	var value: String = str(ProjectSettings.get_setting(AUTOLOAD_SETTING, "")).trim_prefix("*")
	if value.begins_with("uid://"):
		var id: int = ResourceUID.text_to_id(value)
		return ResourceUID.get_id_path(id) if ResourceUID.has_id(id) else value
	return value


static func _describe_mutation_setting() -> void:
	ProjectSettings.add_property_info({"name": ALLOW_MUTATION_SETTING, "type": TYPE_BOOL})
	ProjectSettings.set_as_basic(ALLOW_MUTATION_SETTING, true)
