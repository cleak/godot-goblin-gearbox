class_name HarbourTown
extends Node2D
## The row of houses behind the 2D showcase's quay: plaster over brick, slate roofs,
## chimneys, a tavern sign that swings in the wind. Their walls take the lamps' light and
## the shadows of everything on the quay. A couple of windows glow from the start; when the
## lighthouse wakes they all light, one after another. Presentation only.
##
## Lit windows are not shaded by the 2D lights, but the night's ambient still darkens them:
## each frame they are divided by it (`ambient`), so they keep their own warm colour.

## Each house: x of its left wall, width, height of its eaves above the street.
const LOTS: Array[Vector3] = [
	Vector3(-190.0, 210.0, 400.0),
	Vector3(20.0, 170.0, 500.0),
	Vector3(190.0, 200.0, 360.0),
	Vector3(390.0, 150.0, 460.0),
	Vector3(540.0, 250.0, 390.0),
	Vector3(790.0, 160.0, 490.0),
	Vector3(950.0, 190.0, 350.0),
	Vector3(1140.0, 170.0, 250.0),
]
const TINTS: Array[Color] = [
	Color(0.66, 0.5, 0.3),
	Color(0.32, 0.5, 0.5),
	Color(0.66, 0.42, 0.38),
	Color(0.4, 0.46, 0.58),
	Color(0.72, 0.66, 0.54),
	Color(0.42, 0.53, 0.38),
	Color(0.66, 0.44, 0.3),
	Color(0.55, 0.56, 0.58),
]
## 0 a front gable, 1 a mansard with a dormer, 2 a flat roof behind a parapet.
const ROOFS: Array[int] = [0, 1, 0, 1, 2, 0, 0, 2]
const SLATE: Color = Color(0.36, 0.4, 0.48)
const WINDOW: Vector2 = Vector2(30.0, 46.0)
const WARM: Color = Color(1.0, 0.68, 0.34)
## Where the tavern sign hangs: on the house with this index.
const TAVERN: int = 4
const LIGHT_UP_SECONDS: float = 2.4

## 0 dark to 1 lit: how far the waking has lit the town, as shown.
var lit_shown: float = 0.0
var motion_scale: float = 1.0
## The night's ambient colour now (HarbourWeather.ambient()).
var ambient: Color = Color.WHITE

var _street_y: float = 0.0
## Each lit pane and when, from 0 to 1 of the waking, it comes on (below 0: on from the start).
var _panes: Array[Polygon2D] = []
var _pane_at: Array[float] = []
var _house_lights: Array[PointLight2D] = []
var _sign: Node2D = null
var _sign_swing: Spring = Spring.new(Vector3.ZERO, 30.0, 1.6)
var _time: float = 0.0
var _random: RandomNumberGenerator = RandomNumberGenerator.new()
var _light_tween: Tween = null


## Builds the row standing on `street_y`; windows keep clear of the lamps at `avoid` (x).
func build(street_y: float, avoid: Array[float]) -> void:
	_street_y = street_y
	_random.seed = 21
	var plaster: ShaderMaterial = Paint.masonry(0, 0.5)
	var slate: ShaderMaterial = Paint.masonry(1, 0.8)
	for i: int in range(LOTS.size()):
		_build_house(i, plaster, slate, avoid)
	_build_sign()


## The lighthouse woke: every window comes on, one after another.
func light_up() -> void:
	if _light_tween != null:
		_light_tween.kill()
	_light_tween = create_tween()
	_light_tween.tween_property(self, ^"lit_shown", 1.0, LIGHT_UP_SECONDS)


## Shows the town lit or dark at once: for a reset.
func show_lit(value: bool) -> void:
	if _light_tween != null:
		_light_tween.kill()
	lit_shown = 1.0 if value else 0.0


func _process(delta: float) -> void:
	_time += delta
	var lift: Color = Color(
		1.0 / maxf(ambient.r, 0.05), 1.0 / maxf(ambient.g, 0.05), 1.0 / maxf(ambient.b, 0.05)
	)
	for i: int in range(_panes.size()):
		var on: float = clampf((lit_shown - _pane_at[i]) * 6.0, 0.0, 1.0)
		if _pane_at[i] < 0.0:
			on = 0.35 + 0.65 * lit_shown
		var flicker: float = 0.94 + 0.06 * sin(_time * (2.0 + i * 0.37) + i)
		_panes[i].self_modulate = Color(lift * 0.9 * flicker, on)
	for light: PointLight2D in _house_lights:
		light.energy = 0.55 * lit_shown
	var gust: float = sin(_time * 0.9) * 0.08 + sin(_time * 2.3) * 0.03
	_sign_swing.target = Vector3(gust * motion_scale, 0.0, 0.0)
	_sign.rotation = _sign_swing.step(delta).x


func _build_house(
	index: int, plaster: ShaderMaterial, slate: ShaderMaterial, avoid: Array[float]
) -> void:
	var lot: Vector3 = LOTS[index]
	var left: float = lot.x
	var width: float = lot.y
	var eaves: float = _street_y - lot.z
	var tint: Color = TINTS[index]
	var house: Node2D = Node2D.new()
	house.name = "House%d" % index
	add_child(house)
	var outline: PackedVector2Array = Paint.box(Vector2(left, eaves), Vector2(width, lot.z))
	var roof: int = ROOFS[index]
	if roof == 0:
		var peak: float = eaves - width * 0.42
		outline = PackedVector2Array(
			[
				Vector2(left, _street_y),
				Vector2(left, eaves),
				Vector2(left + width * 0.5, peak),
				Vector2(left + width, eaves),
				Vector2(left + width, _street_y),
			]
		)
		# Slate along both slopes of the gable, overhanging the walls.
		for way: float in [-1.0, 1.0]:
			var foot: Vector2 = Vector2(
				left + width * 0.5 + way * (width * 0.5 + 12.0), eaves + 6.0
			)
			var top: Vector2 = Vector2(left + width * 0.5, peak - 14.0)
			var normal: Vector2 = (top - foot).orthogonal().normalized() * 16.0 * way
			Paint.polygon(
				house, PackedVector2Array([foot, top, top - normal, foot - normal]), SLATE, slate
			)
		_chimney(house, Vector2(left + width * 0.72, eaves - width * 0.2), tint)
	elif roof == 1:
		var crown: float = eaves - 70.0
		(
			Paint
			. polygon(
				house,
				PackedVector2Array(
					[
						Vector2(left - 10.0, eaves),
						Vector2(left + 16.0, crown),
						Vector2(left + width - 16.0, crown),
						Vector2(left + width + 10.0, eaves),
					]
				),
				SLATE,
				slate
			)
		)
		_chimney(house, Vector2(left + width * 0.2, crown), tint)
		var dormer: Vector2 = Vector2(left + width * 0.5 - 22.0, crown + 12.0)
		Paint.polygon(house, Paint.box(dormer, Vector2(44.0, 50.0)), tint, plaster)
		Paint.polygon(
			house,
			PackedVector2Array(
				[
					dormer + Vector2(-6.0, 2.0),
					dormer + Vector2(22.0, -18.0),
					dormer + Vector2(50.0, 2.0)
				]
			),
			SLATE,
			slate
		)
		_window(house, dormer + Vector2(7.0, 10.0), Vector2(30.0, 36.0), tint)
	else:
		_chimney(house, Vector2(left + width * 0.75, eaves - 8.0), tint)
	Paint.polygon(house, outline, tint, plaster)
	# Cornice and plinth, darker, to sit the house on the street.
	Paint.polygon(
		house,
		Paint.box(Vector2(left - 6.0, eaves - 2.0), Vector2(width + 12.0, 9.0)),
		tint.darkened(0.45)
	)
	Paint.polygon(
		house,
		Paint.box(Vector2(left, _street_y - 22.0), Vector2(width, 22.0)),
		tint.darkened(0.5),
		plaster
	)
	if roof == 2:
		Paint.polygon(
			house,
			Paint.box(Vector2(left - 4.0, eaves - 26.0), Vector2(width + 8.0, 26.0)),
			tint.darkened(0.15),
			plaster
		)
	# A drainpipe down one side.
	Paint.polygon(
		house,
		Paint.box(Vector2(left + width - 9.0, eaves + 6.0), Vector2(5.0, lot.z - 6.0)),
		Color(0.12, 0.13, 0.15)
	)
	# The door, near the middle, and windows in rows above it.
	var door_x: float = left + width * (0.3 if index % 2 == 0 else 0.6) - 17.0
	Paint.polygon(
		house,
		Paint.rounded(Vector2(door_x - 5.0, _street_y - 82.0), Vector2(44.0, 82.0), 20.0),
		tint.darkened(0.55)
	)
	Paint.polygon(
		house,
		Paint.rounded(Vector2(door_x, _street_y - 76.0), Vector2(34.0, 76.0), 16.0),
		Color(0.22, 0.13, 0.08)
	)
	Paint.polygon(
		house,
		Paint.ellipse(Vector2(door_x + 26.0, _street_y - 38.0), Vector2(2.5, 2.5), 8),
		Color(0.8, 0.62, 0.3)
	)
	var columns: int = maxi(1, int((width - 30.0) / 62.0))
	var rows: int = maxi(1, int((lot.z - 120.0) / 100.0))
	var pitch: float = (width - columns * WINDOW.x) / (columns + 1)
	for row: int in range(rows):
		for column: int in range(columns):
			var x: float = left + pitch + column * (WINDOW.x + pitch)
			var y: float = eaves + 34.0 + row * 100.0
			if _near_any(x + WINDOW.x * 0.5, avoid, 40.0):
				continue
			_window(house, Vector2(x, y), WINDOW, tint)
	# A ground-floor window beside the door, if there is room.
	var shop_x: float = door_x + (60.0 if index % 2 == 0 else -76.0)
	if (
		shop_x > left + 8.0
		and shop_x + 52.0 < left + width - 12.0
		and not _near_any(shop_x + 26.0, avoid, 46.0)
	):
		_window(house, Vector2(shop_x, _street_y - 86.0), Vector2(52.0, 56.0), tint)
	var glow: PointLight2D = PointLight2D.new()
	glow.texture = Paint.soft_light(512)
	glow.texture_scale = width / 220.0
	glow.color = WARM
	glow.energy = 0.0
	glow.height = 80.0
	glow.position = Vector2(left + width * 0.5, eaves + lot.z * 0.4)
	house.add_child(glow)
	_house_lights.append(glow)


func _chimney(house: Node2D, foot: Vector2, tint: Color) -> void:
	Paint.polygon(
		house,
		Paint.box(foot + Vector2(-11.0, -60.0), Vector2(22.0, 70.0)),
		Color(0.5, 0.3, 0.24),
		Paint.masonry(0, 0.4)
	)
	Paint.polygon(
		house, Paint.box(foot + Vector2(-14.0, -66.0), Vector2(28.0, 7.0)), tint.darkened(0.5)
	)
	Paint.polygon(
		house, Paint.box(foot + Vector2(-6.0, -78.0), Vector2(12.0, 13.0)), Color(0.35, 0.2, 0.15)
	)


func _window(house: Node2D, corner: Vector2, size: Vector2, tint: Color) -> void:
	var frame: Color = Color(0.74, 0.72, 0.67).lerp(tint, 0.3)
	Paint.polygon(house, Paint.box(corner - Vector2(4.0, 4.0), size + Vector2(8.0, 8.0)), frame)
	Paint.polygon(
		house,
		Paint.box(corner + Vector2(-7.0, size.y + 2.0), Vector2(size.x + 14.0, 6.0)),
		frame.darkened(0.2)
	)
	Paint.polygon(house, Paint.box(corner, size), Color(0.05, 0.06, 0.09))
	# The lit pane: warm, brighter low down where the lamp inside stands.
	var pane: Polygon2D = Paint.polygon(
		house, Paint.box(corner, size), Color.WHITE, Paint.additive()
	)
	pane.vertex_colors = PackedColorArray(
		[
			Color(0.7, 0.4, 0.18),
			Color(0.7, 0.4, 0.18),
			Color(1.0, 0.72, 0.38),
			Color(1.0, 0.72, 0.38)
		]
	)
	pane.self_modulate = Color(1.0, 1.0, 1.0, 0.0)
	_panes.append(pane)
	var order: float = _random.randf_range(0.05, 0.85)
	if _random.randf() < 0.12:
		order = -1.0
	_pane_at.append(order)
	# A curtain drawn across part of some windows.
	if _random.randf() < 0.45:
		var drawn: float = size.x * _random.randf_range(0.3, 0.5)
		var curtain_x: float = corner.x if _random.randf() < 0.5 else corner.x + size.x - drawn
		Paint.polygon(
			house,
			Paint.box(Vector2(curtain_x, corner.y), Vector2(drawn, size.y)),
			Color(0.24, 0.08, 0.07, 0.85)
		)
	# Glazing bars over everything.
	var bars: Color = frame.darkened(0.3)
	Paint.polygon(
		house, Paint.box(corner + Vector2(size.x * 0.5 - 1.5, 0.0), Vector2(3.0, size.y)), bars
	)
	Paint.polygon(
		house, Paint.box(corner + Vector2(0.0, size.y * 0.45 - 1.5), Vector2(size.x, 3.0)), bars
	)


## The tavern's sign: a board on two chains from an iron bracket, swinging in the wind.
func _build_sign() -> void:
	var lot: Vector3 = LOTS[TAVERN]
	var at: Vector2 = Vector2(lot.x + lot.y - 24.0, _street_y - 150.0)
	var iron: Color = Color(0.12, 0.12, 0.14)
	Paint.polygon(self, Paint.box(at + Vector2(-4.0, -4.0), Vector2(76.0, 5.0)), iron)
	Paint.polygon(self, Paint.box(at + Vector2(-4.0, -4.0), Vector2(5.0, 24.0)), iron)
	_sign = Node2D.new()
	_sign.name = "Sign"
	_sign.position = at + Vector2(40.0, 0.0)
	add_child(_sign)
	for chain: float in [-24.0, 24.0]:
		Paint.polygon(_sign, Paint.box(Vector2(chain - 1.0, 0.0), Vector2(2.0, 14.0)), iron)
	var board: PackedVector2Array = Paint.rounded(Vector2(-34.0, 14.0), Vector2(68.0, 44.0), 6.0)
	Paint.polygon(_sign, board, Color(0.36, 0.22, 0.12))
	Paint.polygon(
		_sign, Paint.rounded(Vector2(-30.0, 18.0), Vector2(60.0, 36.0), 4.0), Color(0.18, 0.28, 0.3)
	)
	# Painted on it: a brass gear with a lamp's flame in its middle.
	var gear: PackedVector2Array = PackedVector2Array()
	for tooth: int in range(24):
		var radius: float = 13.0 if tooth % 2 == 0 else 10.0
		gear.append(Vector2(0.0, 36.0) + Vector2.from_angle(TAU * tooth / 24.0) * radius)
	Paint.polygon(_sign, gear, Color(0.85, 0.64, 0.3))
	Paint.polygon(
		_sign, Paint.ellipse(Vector2(0.0, 36.0), Vector2(6.0, 6.0), 12), Color(0.18, 0.28, 0.3)
	)
	Paint.polygon(
		_sign,
		PackedVector2Array(
			[Vector2(0.0, 30.0), Vector2(3.0, 37.0), Vector2(0.0, 40.0), Vector2(-3.0, 37.0)]
		),
		Color(1.0, 0.7, 0.3)
	)
	Paint.occluder(_sign, board)


static func _near_any(x: float, points: Array[float], gap: float) -> bool:
	for point: float in points:
		if absf(x - point) < gap:
			return true
	return false
