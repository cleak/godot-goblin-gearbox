// A sea of cloud below the island: drifting noise, brighter where the moon catches it.
shader_type spatial;
render_mode unshaded, depth_draw_never, cull_disabled;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 shadow_color : source_color = vec3(0.10, 0.13, 0.27);
uniform vec3 lit_color : source_color = vec3(0.55, 0.62, 0.85);
uniform vec3 glow_color : source_color = vec3(0.20, 0.90, 0.65);
uniform vec2 drift = vec2(0.012, 0.006);
uniform float scale = 0.07;
uniform float density = 0.6;
uniform float awake : hint_range(0.0, 1.0) = 0.0;

varying vec3 world_pos;

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec2 p = world_pos.xz * scale + TIME * drift;
	float base = fbm(p);
	float detail = fbm(p * 3.1 - TIME * drift * 2.0);
	float cloud = smoothstep(1.0 - density, 1.0, base + detail * 0.35);
	// Fade out under the island and towards the far edge.
	float radius = length(world_pos.xz);
	float fade = smoothstep(4.0, 9.0, radius) * smoothstep(70.0, 40.0, radius);
	// Thick, billowing tops catch the light; thin edges and hollows stay in shade.
	float light = smoothstep(0.35, 0.8, detail * 0.6 + cloud * 0.6);
	vec3 colour = mix(shadow_color, lit_color, light);
	colour += glow_color * awake * 0.25 * light;
	ALBEDO = colour;
	ALPHA = cloud * fade;
}
