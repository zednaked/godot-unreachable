extends Control
# Only the old menu scene uses this script, and only this script opens that scene.
func reopen() -> void:
	get_tree().change_scene_to_file("res://scenes/old_menu.tscn")
