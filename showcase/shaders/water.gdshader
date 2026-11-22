// The spring pool: clear water over a stone bed. The bed shows through, bent by the
// ripples and tinted by depth (red is absorbed first); caustics wander over it; the
// surface reflects the sky where it is seen at a slant, and foams where it meets the bank.
shader_type spatial;
render_mode depth_draw_always, cull_back;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 deep_color : source_color = vec3(0.01, 0.05, 0.07);
uniform vec3 foam_color : source_color = vec3(0.75, 0.85, 0.95);
// Per unit of water the light crosses: red goes first, then green, so depth turns teal.
uniform vec3 absorption = vec3(5.5, 2.2, 1.7);
uniform float bend = 0.035;
uniform float flow = 0.35;
uniform sampler2D screen_texture : hint_screen_texture, filter_linear_mipmap;
uniform sampler2D depth_texture : hint_depth_texture, filter_linear;

varying vec3 world_pos;

float height_at(vec2 p) {
	return value_noise(p * 4.0 + vec2(TIME * 0.6, TIME * 0.3)) * 0.6
		+ value_noise(p * 9.0 - vec2(TIME * 0.4, -TIME * 0.7)) * 0.4;
}

// Bright threads where two drifting ripple fields cross, like light focused by waves.
float caustic(vec2 p) {
	float a = 1.0 - abs(value_noise(p * 5.0 + vec2(TIME * 0.5, TIME * 0.2)) * 2.0 - 1.0);
	float b = 1.0 - abs(value_noise(p * 6.3 - vec2(TIME * 0.3, -TIME * 0.45)) * 2.0 - 1.0);
	return pow(a * b, 6.0);
}

// View-space distance from this surface to whatever the depth buffer holds at `uv`.
float thickness_at(vec2 uv, float surface_z, mat4 inv_projection) {
	float raw = texture(depth_texture, uv).r;
	vec4 behind = inv_projection * vec4(uv * 2.0 - 1.0, raw, 1.0);
	return -behind.z / behind.w + surface_z;
}

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec2 p = world_pos.xz - vec2(0.0, TIME * flow);
	float e = 0.02;
	float hx = height_at(p + vec2(e, 0.0)) - height_at(p - vec2(e, 0.0));
	float hz = height_at(p + vec2(0.0, e)) - height_at(p - vec2(0.0, e));
	vec3 ripple = normalize(vec3(-hx * 0.6, 1.0, -hz * 0.6));
	NORMAL = normalize((VIEW_MATRIX * vec4(ripple, 0.0)).xyz);
	// Refraction: look through the surface a little off-line, unless that would pick up
	// something in front of the water (then look straight through).
	vec2 uv = SCREEN_UV + ripple.xz * bend;
	float thickness = thickness_at(uv, VERTEX.z, INV_PROJECTION_MATRIX);
	if (thickness < 0.0) {
		uv = SCREEN_UV;
		thickness = thickness_at(uv, VERTEX.z, INV_PROJECTION_MATRIX);
	}
	thickness = clamp(thickness, 0.0, 2.0);
	vec3 bed = textureLod(screen_texture, uv, 0.0).rgb;
	vec3 through = exp(-absorption * thickness);
	// Caustics on the bed, fainter the deeper it lies.
	float glints = caustic(world_pos.xz + ripple.xz * 0.3) * (1.0 - smoothstep(0.1, 0.6, thickness));
	bed *= 1.0 + glints * 2.5;
	float foam = smoothstep(0.035, 0.0, thickness) * (0.4 + 0.6 * value_noise(p * 12.0));
	// What the bed sends through the water is light, not surface colour: emission.
	// The water's own body scatters a little of the scene's light: albedo.
	float clarity = (through.r + through.g + through.b) / 3.0;
	ALBEDO = mix(deep_color * (1.0 - clarity), foam_color, foam);
	EMISSION = bed * through * (1.0 - foam);
	ROUGHNESS = mix(0.02, 0.6, foam);
	SPECULAR = 0.5;
	METALLIC = 0.0;
}
