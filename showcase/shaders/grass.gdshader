// Grass blades from a MultiMesh: they sway in gusts and part around the goblin.
// UV.y is 0 at the root and 1 at the tip; the instance colour varies each blade.
shader_type spatial;
render_mode cull_disabled, world_vertex_coords;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 root_color : source_color = vec3(0.05, 0.13, 0.07);
uniform vec3 tip_color : source_color = vec3(0.35, 0.58, 0.26);
uniform vec3 glow_color : source_color = vec3(0.55, 1.0, 0.75);
uniform float wind_strength = 0.12;
// Where the goblin stands, in world space; blades lean away from it.
uniform vec3 push_origin = vec3(0.0, -100.0, 0.0);
uniform float push_radius = 0.55;
// 0 asleep, 1 the gearbox is awake: the tips catch the aurora.
uniform float awake : hint_range(0.0, 1.0) = 0.0;

varying float tip;
// Where the blade stands, for patches of colour across the lawn.
varying vec2 ground;

void vertex() {
	tip = UV.y;
	ground = VERTEX.xz;
	float bend = tip * tip;
	float gust = fbm(VERTEX.xz * 0.35 + vec2(TIME * 0.22, TIME * 0.13));
	float flutter = sin(TIME * 2.7 + VERTEX.x * 3.1 + VERTEX.z * 2.3) * 0.25;
	vec2 wind = vec2(0.8, 0.45) * (gust * 1.6 - 0.4 + flutter) * wind_strength;
	vec2 away = VERTEX.xz - push_origin.xz;
	float near = smoothstep(push_radius, 0.0, length(away)) * step(abs(VERTEX.y - push_origin.y), 0.6);
	vec2 push = normalize(away + 1e-4) * near * 0.22;
	VERTEX.xz += (wind + push) * bend;
	VERTEX.y -= near * 0.08 * bend;
	// Lit like the ground under it, so the lawn reads as one soft surface.
	NORMAL = vec3(0.0, 1.0, 0.0);
}

void fragment() {
	vec3 colour = mix(root_color, tip_color, tip) * COLOR.rgb;
	// Lush and sparse patches, and drifts where the grass has gone to seed.
	float lush = fbm(ground * 0.8);
	float seed = smoothstep(0.6, 0.72, fbm(ground * 0.6 + vec2(5.3, 1.7)));
	colour *= mix(0.72, 1.18, lush);
	colour = mix(colour, vec3(0.5, 0.5, 0.2) * COLOR.rgb, seed * tip * 0.3);
	ALBEDO = colour;
	ROUGHNESS = 0.85;
	SPECULAR = 0.2;
	BACKLIGHT = vec3(0.25, 0.4, 0.2) * tip;
	EMISSION = glow_color * awake * tip * tip * 0.18;
}
