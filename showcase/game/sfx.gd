class_name Sfx
extends Node
## The showcase's sounds, synthesised at start: no audio files. Each beat has one, and each
## play varies its pitch a little (docs/feel.md). Its own random numbers, never the rules'.

const MIX_RATE: int = 22050
const VOICES: int = 8
const PITCH_JITTER: float = 0.07

## Name -> the generated sound; built once per process, shared by every scene.
static var _sounds: Dictionary[StringName, AudioStreamWAV] = {}

var _players: Array[AudioStreamPlayer] = []
var _next_voice: int = 0
var _random: RandomNumberGenerator = RandomNumberGenerator.new()


func _ready() -> void:
	_random.seed = 7
	if _sounds.is_empty():
		_sounds[&"hop"] = _wav(_tone([620.0, 880.0], 0.09, 0.0, 0.35))
		_sounds[&"bump"] = _wav(_tone([150.0, 90.0], 0.12, 0.25, 0.5))
		_sounds[&"ignite"] = _mix(_noise_burst(0.45, 0.5), _tone([440.0, 660.0], 0.6, 0.0, 0.3))
		_sounds[&"chime"] = _wav(_chord([659.3, 987.8, 1318.5], 1.2, 0.25))
		_sounds[&"clunk"] = _wav(_tone([95.0, 60.0], 0.35, 0.45, 0.6))
		_sounds[&"wake"] = _wav(_chord([130.8, 196.0, 261.6, 329.6, 392.0], 3.5, 0.32))
	for voice: int in range(VOICES):
		var player: AudioStreamPlayer = AudioStreamPlayer.new()
		player.name = "Voice%d" % voice
		add_child(player)
		_players.append(player)


func play(sound: StringName, volume_db: float = 0.0) -> void:
	assert(_sounds.has(sound), "no sound named %s" % sound)
	var player: AudioStreamPlayer = _players[_next_voice]
	_next_voice = (_next_voice + 1) % _players.size()
	player.stream = _sounds[sound]
	player.volume_db = volume_db
	player.pitch_scale = 1.0 + _random.randf_range(-PITCH_JITTER, PITCH_JITTER)
	player.play()


## A sweep from the first frequency to the last, with a little noise for grit.
func _tone(sweep: Array[float], seconds: float, grit: float, gain: float) -> PackedFloat32Array:
	var count: int = int(seconds * MIX_RATE)
	var out: PackedFloat32Array = PackedFloat32Array()
	out.resize(count)
	var phase: float = 0.0
	for i: int in range(count):
		var t: float = float(i) / count
		var frequency: float = lerpf(sweep[0], sweep[sweep.size() - 1], t)
		phase += TAU * frequency / MIX_RATE
		var envelope: float = minf(t * 40.0, 1.0) * pow(1.0 - t, 2.0)
		var wave: float = sin(phase) * 0.8 + sin(phase * 2.0) * 0.2
		out[i] = (wave + _random.randf_range(-1.0, 1.0) * grit) * envelope * gain
	return out


## Bell-like partials that ring out together.
func _chord(notes: Array[float], seconds: float, gain: float) -> PackedFloat32Array:
	var count: int = int(seconds * MIX_RATE)
	var out: PackedFloat32Array = PackedFloat32Array()
	out.resize(count)
	for i: int in range(count):
		var t: float = float(i) / MIX_RATE
		var envelope: float = minf(t * 30.0, 1.0) * exp(-t * 4.0 / seconds)
		var sample: float = 0.0
		for note: float in notes:
			sample += sin(TAU * note * t) + 0.3 * sin(TAU * note * 2.01 * t) * exp(-t * 3.0)
		out[i] = sample / notes.size() * envelope * gain
	return out


## A soft whoosh: filtered noise that swells and dies.
func _noise_burst(seconds: float, gain: float) -> PackedFloat32Array:
	var count: int = int(seconds * MIX_RATE)
	var out: PackedFloat32Array = PackedFloat32Array()
	out.resize(count)
	var smooth: float = 0.0
	for i: int in range(count):
		var t: float = float(i) / count
		smooth = lerpf(smooth, _random.randf_range(-1.0, 1.0), 0.12)
		out[i] = smooth * sin(PI * t) * gain
	return out


func _mix(a: PackedFloat32Array, b: PackedFloat32Array) -> AudioStreamWAV:
	var longest: int = maxi(a.size(), b.size())
	var out: PackedFloat32Array = PackedFloat32Array()
	out.resize(longest)
	for i: int in range(longest):
		out[i] = (a[i] if i < a.size() else 0.0) + (b[i] if i < b.size() else 0.0)
	return _wav(out)


func _wav(samples: PackedFloat32Array) -> AudioStreamWAV:
	var bytes: PackedByteArray = PackedByteArray()
	bytes.resize(samples.size() * 2)
	for i: int in range(samples.size()):
		bytes.encode_s16(i * 2, int(clampf(samples[i], -1.0, 1.0) * 32767.0))
	var stream: AudioStreamWAV = AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = MIX_RATE
	stream.stereo = false
	stream.data = bytes
	return stream
