// Worked metal for the gearbox, lanterns and goggles: polished brass or copper with smudged
// roughness and verdigris in the low spots, or iron under enamel paint that has chipped
// back to bare metal. `paint` 0 is bare metal; 1 paint over everything but the chips.
shader_type spatial;

#include "res://showcase/shaders/triplanar.gdshaderinc"

uniform sampler2D grime_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D fine_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D fine_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform vec3 metal_color : source_color = vec3(0.92, 0.7, 0.36);
uniform float metal_roughness = 0.26;
uniform vec3 patina_color : source_color = vec3(0.27, 0.52, 0.44);
uniform float patina = 0.25;
uniform vec3 paint_color : source_color = vec3(0.07, 0.26, 0.27);
uniform float paint = 0.0;
uniform float scale = 1.6;
uniform float normal_strength = 0.35;

varying vec3 world_pos;
varying vec3 world_normal;

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
}

void fragment() {
	vec3 n = normalize(world_normal);
	vec3 w = tri_weights(n);
	float grime = tri_height(grime_height, world_pos * scale, w);
	float fine = tri_height(fine_height, world_pos * scale * 4.0, w);
	vec3 bumped = tri_normal(fine_normal, world_pos * scale * 4.0, n, w, normal_strength);
	// Bare metal: smudges and wear change the roughness more than the colour.
	vec3 albedo = metal_color * mix(0.82, 1.05, fine);
	float rough = clamp(metal_roughness + (grime - 0.5) * 0.25 + (fine - 0.5) * 0.08, 0.05, 1.0);
	float metallic = 1.0;
	// Verdigris gathers where grime is deepest.
	float green = smoothstep(1.0 - patina * 0.5, 1.08 - patina * 0.5, (1.0 - grime) * 0.7 + fine * 0.3);
	green *= step(0.001, patina);
	albedo = mix(albedo, patina_color * mix(0.7, 1.1, fine), green);
	rough = mix(rough, 0.85, green);
	metallic = mix(metallic, 0.0, green);
	// Enamel paint, chipped in small flecks where the grime says the wear is worst.
	float flecks = tri_height(fine_height, world_pos * scale * 11.0, w);
	float chips = smoothstep(0.7, 0.74, flecks * 0.75 + grime * 0.35);
	float painted = paint * (1.0 - chips);
	vec3 enamel = paint_color * mix(0.85, 1.08, grime);
	albedo = mix(albedo, enamel, painted);
	rough = mix(rough, 0.38 + fine * 0.12, painted);
	metallic = mix(metallic, 0.0, painted);
	ALBEDO = albedo;
	METALLIC = metallic;
	ROUGHNESS = rough;
	SPECULAR = 0.5;
	CLEARCOAT = painted * 0.6;
	CLEARCOAT_ROUGHNESS = 0.25;
	NORMAL = normalize((VIEW_MATRIX * vec4(bumped, 0.0)).xyz);
}
