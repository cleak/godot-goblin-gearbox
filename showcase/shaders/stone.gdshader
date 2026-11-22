// Weathered stone: pitted, cracked faces, darker in the cavities, moss where rain and light
// settle (upward faces). The per-stone tint comes from the vertex colour.
shader_type spatial;

#include "res://showcase/shaders/triplanar.gdshaderinc"

uniform sampler2D rock_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D rock_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform sampler2D fine_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D fine_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform vec3 stone_light : source_color = vec3(0.56, 0.53, 0.49);
uniform vec3 stone_dark : source_color = vec3(0.24, 0.23, 0.23);
uniform vec3 moss_light : source_color = vec3(0.36, 0.46, 0.14);
uniform vec3 moss_dark : source_color = vec3(0.11, 0.2, 0.06);
uniform float scale = 0.9;
uniform float fine_scale = 3.5;
uniform float normal_strength = 1.4;
uniform float moss = 0.55;
// How much the moss follows height: 1 thick on top faces only, 0 everywhere.
uniform float moss_up = 1.0;

varying vec3 world_pos;
varying vec3 world_normal;

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
}

void fragment() {
	vec3 n = normalize(world_normal);
	vec3 w = tri_weights(n);
	vec3 p = world_pos * scale;
	float h = tri_height(rock_height, p, w);
	float f = tri_height(fine_height, world_pos * fine_scale, w);
	vec3 bumped = tri_normal(rock_normal, p, n, w, normal_strength);
	bumped = tri_normal(fine_normal, world_pos * fine_scale, bumped, w, 0.7);
	float cavity = smoothstep(0.05, 0.55, h);
	vec3 rock = mix(stone_dark, stone_light, smoothstep(0.25, 0.85, h * 0.7 + f * 0.3));
	rock *= COLOR.rgb;
	rock *= mix(0.45, 1.0, cavity);
	float facing = mix(1.0, smoothstep(0.2, 0.85, bumped.y), moss_up);
	float growth = smoothstep(0.45, 0.75, f * 0.6 + (1.0 - h) * 0.4 + facing * 0.35);
	float moss_mask = clamp(growth * facing * moss * 1.6, 0.0, 1.0);
	vec3 green = mix(moss_dark, moss_light, f);
	ALBEDO = mix(rock, green, moss_mask);
	ROUGHNESS = mix(mix(0.95, 0.68, h), 0.92, moss_mask);
	SPECULAR = 0.45;
	AO = mix(0.35, 1.0, cavity);
	AO_LIGHT_AFFECT = 0.25;
	NORMAL = normalize((VIEW_MATRIX * vec4(bumped, 0.0)).xyz);
	BACKLIGHT = vec3(0.08, 0.12, 0.03) * moss_mask;
}
