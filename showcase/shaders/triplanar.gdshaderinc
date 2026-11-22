// World-space (triplanar) texturing for meshes without UVs: three projections blended by the
// surface normal. Normal maps use the whiteout blend (Ben Golus, "Normal Mapping for a
// Triplanar Shader", 2017), so detail follows the surface on every side.

vec3 tri_weights(vec3 n) {
	vec3 w = pow(abs(n), vec3(4.0));
	return w / (w.x + w.y + w.z);
}

float tri_height(sampler2D tex, vec3 p, vec3 w) {
	return texture(tex, p.zy).r * w.x + texture(tex, p.xz).r * w.y + texture(tex, p.xy).r * w.z;
}

// `n` is the world normal to perturb; `strength` scales the map's slope.
vec3 tri_normal(sampler2D tex, vec3 p, vec3 n, vec3 w, float strength) {
	vec3 tx = texture(tex, p.zy).xyz * 2.0 - 1.0;
	vec3 ty = texture(tex, p.xz).xyz * 2.0 - 1.0;
	vec3 tz = texture(tex, p.xy).xyz * 2.0 - 1.0;
	tx.xy *= strength;
	ty.xy *= strength;
	tz.xy *= strength;
	tx = vec3(tx.xy + n.zy, abs(tx.z) * n.x);
	ty = vec3(ty.xy + n.xz, abs(ty.z) * n.y);
	tz = vec3(tz.xy + n.xy, abs(tz.z) * n.z);
	return normalize(tx.zyx * w.x + ty.xzy * w.y + tz.xyz * w.z);
}
