extends Node
func _ready():
	assert(Inventory.new().items.is_empty())
	var fixture = load("res://tests/fixtures/bag.tres")
	add_child(load("res://scripts/debug_hitboxes.gd").new())
