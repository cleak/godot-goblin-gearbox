// Turf on the terrace tops: a mat of moss and grass with bare, damp earth showing through;
// the sides below it are earth (mostly hidden by the stone walls).
shader_type spatial;

#include "res://showcase/shaders/triplanar.gdshaderinc"

uniform sampler2D soil_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D soil_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform sampler2D fine_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D fine_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform vec3 grass_dark : source_color = vec3(0.12, 0.21, 0.06);
uniform vec3 grass_light : source_color = vec3(0.33, 0.45, 0.13);
uniform vec3 earth_dark : source_color = vec3(0.12, 0.08, 0.06);
uniform vec3 earth_light : source_color = vec3(0.33, 0.24, 0.16);
uniform float scale = 0.7;

varying vec3 world_pos;
varying vec3 world_normal;

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
}

void fragment() {
	vec3 n = normalize(world_normal);
	vec3 w = tri_weights(n);
	float soil = tri_height(soil_height, world_pos * scale, w);
	float fine = tri_height(fine_height, world_pos * 4.0, w);
	vec3 bumped = tri_normal(soil_normal, world_pos * scale, n, w, 0.9);
	bumped = tri_normal(fine_normal, world_pos * 4.0, bumped, w, 0.8);
	float top = smoothstep(0.5, 0.85, n.y);
	float bare = smoothstep(0.58, 0.72, soil) * top;
	vec3 grass = mix(grass_dark, grass_light, smoothstep(0.2, 0.9, fine * 0.6 + soil * 0.4));
	vec3 earth = mix(earth_dark, earth_light, smoothstep(0.25, 0.8, fine));
	vec3 albedo = mix(earth, grass, top * (1.0 - bare));
	ALBEDO = albedo;
	ROUGHNESS = mix(0.92, 0.75, bare);
	SPECULAR = 0.35;
	AO = mix(0.55, 1.0, fine);
	AO_LIGHT_AFFECT = 0.3;
	NORMAL = normalize((VIEW_MATRIX * vec4(bumped, 0.0)).xyz);
	BACKLIGHT = vec3(0.1, 0.16, 0.04) * top * (1.0 - bare);
}
