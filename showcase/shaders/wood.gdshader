// Weathered wood and bark. The grain runs along the object's own Y axis, so a post or a
// trunk keeps its grain however it is turned.
shader_type spatial;

#include "res://showcase/shaders/triplanar.gdshaderinc"

uniform sampler2D grain_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D grain_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform vec3 wood_light : source_color = vec3(0.42, 0.28, 0.17);
uniform vec3 wood_dark : source_color = vec3(0.16, 0.1, 0.07);
// Grain stretch: small x and z, large y gives long fibres along the object's Y.
uniform vec3 stretch = vec3(5.0, 0.6, 5.0);
uniform float normal_strength = 1.6;
uniform float roughness_base = 0.78;
uniform vec3 moss_color : source_color = vec3(0.2, 0.3, 0.1);
uniform float moss = 0.0;

varying vec3 object_pos;
varying vec3 object_normal;
varying float up;

void vertex() {
	object_pos = VERTEX;
	object_normal = NORMAL;
	up = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz).y;
}

void fragment() {
	vec3 n = normalize(object_normal);
	vec3 w = tri_weights(n);
	vec3 p = object_pos * stretch;
	float g = tri_height(grain_height, p, w);
	vec3 bumped = tri_normal(grain_normal, p, n, w, normal_strength);
	vec3 world_bumped = normalize((MODEL_MATRIX * vec4(bumped, 0.0)).xyz);
	vec3 albedo = mix(wood_dark, wood_light, smoothstep(0.2, 0.85, g));
	float lichen = moss * smoothstep(0.3, 0.9, up) * smoothstep(0.45, 0.7, g);
	ALBEDO = mix(albedo, moss_color, lichen);
	ROUGHNESS = clamp(roughness_base + (0.5 - g) * 0.2, 0.3, 1.0);
	SPECULAR = 0.35;
	AO = mix(0.5, 1.0, smoothstep(0.1, 0.6, g));
	AO_LIGHT_AFFECT = 0.2;
	NORMAL = normalize((VIEW_MATRIX * vec4(world_bumped, 0.0)).xyz);
}
