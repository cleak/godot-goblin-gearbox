// Tree crowns: lumpy masses of leaves (MeshKit.canopy). A cellular map in world space breaks
// each mass into clumps of leaves that catch the light one by one; the hollows between them
// stay dark, light comes through the thin edges (translucency), and the crowns sway.
shader_type spatial;

#include "res://showcase/shaders/triplanar.gdshaderinc"

uniform sampler2D clump_height : hint_default_white, filter_linear_mipmap, repeat_enable;
uniform sampler2D clump_normal : hint_normal, filter_linear_mipmap, repeat_enable;
uniform vec3 deep_color : source_color = vec3(0.08, 0.17, 0.06);
uniform vec3 leaf_color : source_color = vec3(0.2, 0.4, 0.1);
uniform vec3 tip_color : source_color = vec3(0.52, 0.64, 0.18);
uniform float clump_scale = 1.2;
uniform float sway = 0.03;
uniform float awake : hint_range(0.0, 1.0) = 0.0;
instance uniform float phase = 0.0;
// Each tree's own hue, a little off the others.
instance uniform vec3 tint : source_color = vec3(1.0);

varying vec3 world_pos;
varying vec3 world_normal;

void vertex() {
	// The pattern is fixed to the crown at rest, so it does not swim as the crown sways.
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
	float reach = max(VERTEX.y - 0.7, 0.0);
	float t = TIME * 1.1 + phase;
	VERTEX.x += sin(t + VERTEX.y * 1.7) * sway * reach;
	VERTEX.z += cos(t * 0.8 + VERTEX.x * 1.3) * sway * reach;
}

void fragment() {
	vec3 n = normalize(world_normal);
	vec3 w = tri_weights(n);
	vec3 p = world_pos * clump_scale;
	float clump = tri_height(clump_height, p, w);
	float small = tri_height(clump_height, p * 3.1 + 0.37, w);
	vec3 bumped = tri_normal(clump_normal, p, n, w, 0.9);
	bumped = tri_normal(clump_normal, p * 3.1 + 0.37, bumped, w, 0.25);
	// 0 in the hollows between clumps, 1 on their crowns.
	float lift = smoothstep(0.1, 0.75, clump * 0.75 + small * 0.25);
	float up = smoothstep(-0.2, 0.9, n.y);
	vec3 colour = mix(deep_color, leaf_color, lift);
	colour = mix(colour, tip_color, lift * up * 0.55);
	ALBEDO = colour * tint;
	ROUGHNESS = mix(0.85, 0.6, lift);
	SPECULAR = 0.4;
	AO = mix(0.5, 1.0, lift) * mix(0.6, 1.0, up);
	AO_LIGHT_AFFECT = 0.45;
	NORMAL = normalize((VIEW_MATRIX * vec4(bumped, 0.0)).xyz);
	BACKLIGHT = leaf_color * vec3(1.0, 1.1, 0.5) * 0.55 * lift;
	EMISSION = vec3(0.35, 1.0, 0.75) * awake * 0.035 * lift * up;
}
