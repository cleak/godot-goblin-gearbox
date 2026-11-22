// The light the woken gearbox throws into the sky: an additive column with no hard edge.
// UV.y runs from 0 at the crystal to 1 at the top; UV.x goes once round the cylinder.
// Two cylinders share this material: a tight bright core, and a wide faint halo round it
// (`softness` and `intensity` are set per instance).
shader_type spatial;
render_mode unshaded, blend_add, cull_disabled, depth_draw_never, shadows_disabled;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 beam_color : source_color = vec3(0.35, 1.0, 0.85);
uniform float strength = 0.0;
// Higher: the light gathers tighter towards the middle of the column. Keep it above 2:
// lower, the light still falls steeply at the silhouette and the column shows an edge.
instance uniform float softness = 3.0;
instance uniform float intensity = 1.0;

void fragment() {
	// Full where the column faces the eye, nothing at its silhouette: no hard edge.
	float facing = abs(dot(NORMAL, VIEW));
	float body = pow(facing, softness);
	// Long streaks of light drift up the column; a finer roll climbs faster.
	float streak = value_noise(vec2(UV.x * 9.0, UV.y * 1.6 - TIME * 0.35));
	float roll = value_noise(vec2(UV.x * 5.0, UV.y * 16.0 - TIME * 1.8));
	// Brightest at the crystal, thinning into the sky.
	float rise = exp(-UV.y * 2.4) * smoothstep(0.0, 0.012, UV.y) * smoothstep(1.0, 0.6, UV.y);
	float light = body * (0.5 + 0.5 * streak) * (0.8 + 0.2 * roll) * rise;
	ALBEDO = beam_color * light * strength * intensity;
}
