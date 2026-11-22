// Leaf cards: alpha-cut leaves that let light through (translucency), sway with the wind
// more the further they sit from the trunk's root.
shader_type spatial;
render_mode cull_disabled, world_vertex_coords;

uniform sampler2D leaf_texture : source_color, filter_linear_mipmap, repeat_disable;
uniform vec3 tint : source_color = vec3(1.0);
uniform float sway = 0.04;
uniform float awake : hint_range(0.0, 1.0) = 0.0;
instance uniform float phase = 0.0;

varying float height;

void vertex() {
	height = max(VERTEX.y - NODE_POSITION_WORLD.y, 0.0);
	float t = TIME * 1.3 + phase + VERTEX.x * 0.8;
	VERTEX.xz += vec2(sin(t), cos(t * 0.8)) * sway * height;
	VERTEX.y += sin(t * 2.1 + VERTEX.z * 3.0) * sway * 0.25 * height;
	// Round the normals off towards the crown's outside, so the cards shade as one mass.
	vec3 out_dir = normalize(VERTEX - NODE_POSITION_WORLD - vec3(0.0, 0.9, 0.0));
	NORMAL = normalize(mix(NORMAL, out_dir, 0.75));
}

void fragment() {
	vec4 leaf = texture(leaf_texture, UV);
	ALPHA = leaf.a;
	ALPHA_SCISSOR_THRESHOLD = 0.5;
	ALBEDO = leaf.rgb * tint;
	ROUGHNESS = 0.62;
	SPECULAR = 0.4;
	BACKLIGHT = leaf.rgb * vec3(0.9, 1.0, 0.5) * 0.7;
	EMISSION = vec3(0.35, 1.0, 0.75) * awake * 0.04 * leaf.g;
}
