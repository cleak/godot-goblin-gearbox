// Water for the 2D showcase. `near` 0: the open sea out to the horizon, swell lines that
// shrink with distance, the moon's glitter path. `near` 1: the harbour under the quay, rain
// rings, and the lamps' light broken into shimmering columns. Each glitter is a column at a
// world x: `glints[i]` is (x, strength), its colour `glint_colors[i]`.
shader_type canvas_item;
render_mode unshaded;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 surface_color : source_color = vec3(0.07, 0.1, 0.16);
uniform vec3 deep_color : source_color = vec3(0.01, 0.018, 0.035);
uniform float top = 690.0;
uniform float span = 220.0;
uniform float near = 0.0;
uniform float rain : hint_range(0.0, 1.0) = 1.0;
uniform vec2 glints[6];
uniform vec3 glint_colors[6] : source_color;

varying vec2 world;

void vertex() {
	world = (MODEL_MATRIX * vec4(VERTEX, 0.0, 1.0)).xy;
}

void fragment() {
	float depth = clamp((world.y - top) / span, 0.0, 1.0);
	// Far off, the swell is thin lines; near, broad slow bands.
	float scale = mix(0.06, 0.45, mix(depth, 1.0, near));
	vec3 colour = mix(surface_color, deep_color, depth);
	float swell = value_noise(vec2(world.x * 0.012 / scale + TIME * 0.25, world.y * 0.22 / scale));
	colour += vec3(0.05, 0.065, 0.09) * smoothstep(0.55, 0.9, swell);
	// A bright seam where sea meets sky.
	colour += vec3(0.16, 0.18, 0.24) * exp(-(world.y - top) * 0.25) * (1.0 - near);
	// Rain rings on the near water.
	vec2 g = world / vec2(30.0, 12.0);
	vec2 cell = floor(g);
	float seed = hash21(cell);
	float phase = fract(TIME * (0.7 + seed * 0.6) + seed * 13.0);
	vec2 centre = vec2(0.3 + seed * 0.4, 0.5);
	vec2 ring_at = (fract(g) - centre) * vec2(1.0, 2.5);
	float ring = smoothstep(0.06, 0.0, abs(length(ring_at) - phase * 0.45)) * (1.0 - phase);
	colour += vec3(0.25, 0.3, 0.38) * ring * rain * near * step(0.5, seed);
	// Glitter: each light breaks into a column of sparks that widens towards the viewer.
	float wobble = (value_noise(vec2(world.x * 0.03, world.y * 0.25 - TIME * 1.8)) - 0.5) * 18.0;
	for (int i = 0; i < 6; i++) {
		float width = mix(10.0 + depth * 60.0, 26.0, near);
		float dx = (world.x + wobble * mix(depth, 1.0, near) - glints[i].x) / width;
		float column = exp(-dx * dx);
		float bands = value_noise(vec2(world.x * 0.07, world.y * mix(0.9, 0.3, near) + TIME * 1.4 + float(i)));
		float sparkle = smoothstep(0.45, 0.85, bands);
		colour += glint_colors[i] * glints[i].y * column * sparkle * 1.6;
	}
	COLOR = vec4(colour, 1.0);
}
