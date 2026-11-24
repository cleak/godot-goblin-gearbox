// The lighthouse tower in the 2D showcase: a tapered round tower in red and white bands,
// lit by the moon from the right, its courses of stone faintly showing. The tower's local
// origin is the middle of its foot; it rises to -`tall`.
shader_type canvas_item;
render_mode unshaded;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform float foot = 46.0;
uniform float crown = 30.0;
uniform float tall = 330.0;
uniform float band = 55.0;
uniform vec3 paint_a : source_color = vec3(0.62, 0.62, 0.66);
uniform vec3 paint_b : source_color = vec3(0.42, 0.11, 0.1);
uniform vec3 moon_color : source_color = vec3(0.55, 0.65, 0.95);
// The lamp's warm light on the tower's top, 0 dark to 1 awake.
uniform float lamp = 0.0;

varying vec2 local;

void vertex() {
	local = VERTEX;
}

void fragment() {
	float rise = clamp(-local.y / tall, 0.0, 1.0);
	float half_width = mix(foot, crown, rise);
	float across = clamp(local.x / half_width, -1.0, 1.0);
	// The round surface: its normal turns from left to right across the tower.
	vec3 n = vec3(across, 0.0, sqrt(max(1.0 - across * across, 0.0)));
	float moon = max(dot(n, normalize(vec3(0.8, 0.0, 0.6))), 0.0);
	vec3 paint = mix(paint_a, paint_b, step(0.5, fract(-local.y / band)));
	float course = smoothstep(0.0, 0.12, abs(fract(-local.y / 9.0) - 0.5));
	paint *= mix(0.88, 1.0, course) * mix(0.9, 1.05, value_noise(local * 0.2));
	vec3 lit = paint * (0.1 + moon_color * moon * 0.55);
	lit += paint * vec3(1.0, 0.7, 0.4) * lamp * smoothstep(0.75, 1.0, rise) * 1.4;
	COLOR = vec4(lit, COLOR.a);
}
