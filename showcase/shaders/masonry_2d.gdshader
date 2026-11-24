// Stone, plaster and slate for the 2D showcase, drawn from world position: no texture.
// It writes a normal and lights itself with a wet highlight (light()), so the 2D lights shade it: a lamp picks out each
// brick edge and glints on the rain-wet faces. The polygon's colour tints it.
// `kind`: 0 plaster over brick, 1 slate roof, 2 quay ashlar, 3 cobbles.
shader_type canvas_item;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform int kind = 0;
uniform float wet = 0.6;
// Below this world y the stone stays wet and grows weed (the tide line).
uniform float tide = 100000.0;
uniform float relief = 2.2;
// Towards the moon, canvas space (y down): up, right, and out of the screen.
uniform vec3 moon_from = vec3(0.5, -0.55, 0.67);

varying vec2 world;
// How wet and how glossy this pixel is, from fragment() to light().
varying float wetness;
varying float glossiness;

void vertex() {
	world = (MODEL_MATRIX * vec4(VERTEX, 0.0, 1.0)).xy;
}

// A running-bond course of blocks: x is the height (1 on a face, 0 in the joints), y a
// random number per block.
vec2 courses(vec2 at, vec2 size, float stagger, float bevel) {
	float row = floor(at.y / size.y);
	vec2 p = at + vec2(stagger * size.x * mod(row, 2.0), 0.0);
	vec2 cell = floor(p / size);
	vec2 f = fract(p / size);
	vec2 edge = min(f, 1.0 - f) * size;
	float joint = min(edge.x, edge.y);
	float id = hash21(cell + vec2(row * 7.0, 3.0));
	float h = smoothstep(0.0, bevel, joint);
	h *= 0.86 + 0.14 * value_noise(p * 0.18 + id * 10.0);
	return vec2(h, id);
}

// Plaster sheds in patches: 1 where it holds.
float plaster_at(vec2 p) {
	return smoothstep(0.43, 0.47, fbm(p * 0.012) + value_noise(p * 0.08) * 0.06);
}

float height_at(vec2 p) {
	if (kind == 1) {
		vec2 tile = courses(p, vec2(24.0, 13.0), 0.5, 3.0);
		// Each slate overlaps the one below: it rises towards its lower edge.
		float lap = fract(p.y / 13.0);
		return tile.x * (0.55 + 0.45 * lap);
	}
	if (kind == 2) {
		return courses(p, vec2(66.0, 34.0), 0.5, 6.0).x;
	}
	if (kind == 3) {
		vec2 cobble = courses(p, vec2(17.0, 8.0), 0.5, 3.5);
		return pow(cobble.x, 0.6);
	}
	float brick = courses(p, vec2(26.0, 11.0), 0.5, 2.5).x;
	float plaster = plaster_at(p);
	return mix(brick * 0.8, 1.05 + value_noise(p * 0.05) * 0.08, plaster);
}

void fragment() {
	vec3 tint = COLOR.rgb;
	vec2 p = world;
	float h = height_at(p);
	float hx = height_at(p + vec2(1.0, 0.0)) - height_at(p - vec2(1.0, 0.0));
	float hy = height_at(p + vec2(0.0, 1.0)) - height_at(p - vec2(0.0, 1.0));
	// Canvas normals point y down, like the screen.
	NORMAL = normalize(vec3(-hx * relief, -hy * relief, 1.0));
	// The moon is not a 2D light here (it would light the rain): the stone shades its own
	// relief from the moon's direction, under the night's ambient.
	float moonlit = max(dot(NORMAL, normalize(moon_from)), 0.0);
	float grain = value_noise(p * 0.35);
	float blotch = fbm(p * 0.02);
	vec3 albedo;
	float gloss = 0.25;
	if (kind == 1) {
		vec2 tile = courses(p, vec2(24.0, 13.0), 0.5, 3.0);
		albedo = tint * mix(0.7, 1.05, tile.y) * mix(0.85, 1.0, grain);
		albedo *= mix(0.35, 1.0, smoothstep(0.0, 0.5, h));
		gloss = 0.55;
	} else if (kind == 2) {
		vec2 block = courses(p, vec2(66.0, 34.0), 0.5, 6.0);
		albedo = tint * mix(0.78, 1.08, block.y) * mix(0.85, 1.05, blotch);
		albedo *= mix(0.4, 1.0, smoothstep(0.0, 0.6, h));
		gloss = 0.35;
	} else if (kind == 3) {
		vec2 cobble = courses(p, vec2(17.0, 8.0), 0.5, 3.5);
		albedo = tint * mix(0.7, 1.1, cobble.y) * mix(0.3, 1.0, h);
		gloss = 0.7;
	} else {
		vec2 brick = courses(p, vec2(26.0, 11.0), 0.5, 2.5);
		vec3 clay = vec3(0.5, 0.25, 0.17) * mix(0.75, 1.15, brick.y);
		vec3 mortar = vec3(0.42, 0.4, 0.36);
		vec3 bare = mix(mortar, clay, smoothstep(0.1, 0.5, brick.x));
		vec3 paint = tint * mix(0.82, 1.04, blotch) * mix(0.94, 1.0, grain);
		albedo = mix(bare, paint, plaster_at(p));
		gloss = 0.2;
	}
	// Rain runs down the walls in streaks; below the tide line the stone is dark and weedy.
	float streak = value_noise(vec2(p.x * 0.09, p.y * 0.004));
	float soaked = smoothstep(0.55, 0.85, streak) * 0.35;
	float below = smoothstep(tide - 30.0, tide + 10.0, p.y);
	vec3 weed = vec3(0.12, 0.2, 0.1) * mix(0.6, 1.1, grain);
	albedo = mix(albedo, weed, below * smoothstep(0.35, 0.65, blotch + grain * 0.3));
	albedo *= 1.0 - soaked - below * 0.3;
	float shine = wet * (0.35 + soaked + below * 0.6) * smoothstep(0.3, 0.9, h);
	albedo *= mix(0.55, 1.35, moonlit);
	COLOR = vec4(albedo, COLOR.a);
	wetness = shine;
	glossiness = clamp(gloss + shine * 0.3, 0.0, 1.0);
}

// Diffuse from the normal, and a tight highlight where the stone is wet: rain glints in
// the lamplight.
void light() {
	float facing = max(dot(NORMAL, LIGHT_DIRECTION), 0.0);
	vec3 halfway = normalize(LIGHT_DIRECTION + vec3(0.0, 0.0, 1.0));
	float glint = pow(max(dot(NORMAL, halfway), 0.0), mix(8.0, 90.0, glossiness)) * wetness;
	vec3 lit = LIGHT_COLOR.rgb * LIGHT_ENERGY * (COLOR.rgb * facing + vec3(glint) * 1.5);
	LIGHT = vec4(lit, LIGHT_COLOR.a);
}
