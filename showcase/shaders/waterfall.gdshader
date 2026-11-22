// The spring spilling over the island's edge: streaks that fall and thin into mist.
// UV.y runs from 0 at the lip to 1 at the bottom.
shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_never;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 water_color : source_color = vec3(0.55, 0.75, 0.95);
uniform vec3 core_color : source_color = vec3(0.85, 0.95, 1.0);
uniform float speed = 1.4;
uniform float intensity = 1.0;

void fragment() {
	vec2 uv = UV;
	float fall = uv.y * 6.0 - TIME * speed;
	float streaks = value_noise(vec2(uv.x * 22.0, fall));
	streaks = mix(streaks, value_noise(vec2(uv.x * 48.0, fall * 1.7)), 0.4);
	float edges = smoothstep(0.0, 0.18, uv.x) * smoothstep(1.0, 0.82, uv.x);
	float thin = mix(1.0, 0.25, smoothstep(0.2, 1.0, uv.y));
	float body = smoothstep(0.35, 0.8, streaks) * edges * thin;
	ALBEDO = mix(water_color, core_color, smoothstep(0.6, 0.9, streaks)) * intensity;
	ALPHA = clamp(body * 0.85, 0.0, 1.0) * smoothstep(1.0, 0.75, uv.y);
}
