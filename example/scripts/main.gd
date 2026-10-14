extends Node2D

var bag := Inventory.new()

func play(name: String) -> void:
	# Built at runtime: every file in res://sfx/ has to stay.
	var stream := load("res://sfx/%s.ogg" % name)
	$Player.add_child(AudioStreamPlayer.new())

@onready var arrow := preload("res://ui/Right on.webp")
