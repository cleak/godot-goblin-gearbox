// The lighthouse beam in the 2D showcase: an additive wedge from the lamp, brightest at its
// root and along its axis, with rain glittering where it crosses. The wedge is built along
// +x: VERTEX.x / `reach` is how far along, VERTEX.y / (x * `spread`) how far off the axis.
shader_type canvas_item;
render_mode blend_add, unshaded;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 beam_color : source_color = vec3(1.0, 0.9, 0.62);
uniform float reach = 1400.0;
uniform float spread = 0.12;
uniform float strength = 0.0;
uniform float rain : hint_range(0.0, 1.0) = 1.0;

varying vec2 local;
varying vec2 world;

void vertex() {
	local = VERTEX;
	world = (MODEL_MATRIX * vec4(VERTEX, 0.0, 1.0)).xy;
}

void fragment() {
	float along = clamp(local.x / reach, 0.0, 1.0);
	float across = local.y / max(local.x * spread, 1.0);
	float core = exp(-across * across * 2.6);
	float fade = pow(1.0 - along, 1.6) * smoothstep(0.0, 0.02, along);
	// Rain caught in the light: fine falling streaks.
	float drops = value_noise(vec2(world.x * 1.3 + world.y * 0.18, world.y * 0.02 - TIME * 26.0));
	float glitter = smoothstep(0.8, 0.97, drops) * rain;
	float light = core * fade * (0.55 + 0.45 * value_noise(vec2(along * 6.0 - TIME * 0.6, across)));
	COLOR = vec4(beam_color * (light * (0.85 + glitter * 1.2)) * strength, 1.0);
}
