// The sky from golden hour to night. `dusk` 0 is a low gold sun under a clear blue sky;
// 0.5 twilight, rose and violet at the horizon; 1 night, with stars and the moon. `aurora`
// lays green and violet curtains over the night once the gearbox wakes.
// The gradient and aurora are soft and render at quarter resolution; the sun, moon and
// stars are sharp and are added at full resolution on top.
shader_type sky;
render_mode use_quarter_res_pass;

#include "res://showcase/shaders/noise.gdshaderinc"

// Directions towards the sun and the moon; Atmosphere sets them from its lights.
uniform vec3 sun_direction = vec3(-0.69, 0.18, -0.7);
uniform vec3 moon_direction = vec3(0.3, 0.2, -0.93);
uniform float dusk : hint_range(0.0, 1.0) = 0.0;
uniform float aurora : hint_range(0.0, 1.0) = 0.0;
uniform vec3 aurora_low : source_color = vec3(0.1, 0.95, 0.5);
uniform vec3 aurora_high : source_color = vec3(0.55, 0.32, 1.0);
uniform float star_brightness = 2.4;

vec3 stars(vec3 dir) {
	vec3 cell = floor(dir * 260.0);
	float pick = hash31(cell);
	if (pick < 0.985) {
		return vec3(0.0);
	}
	vec3 centre = (cell + 0.5) / 260.0;
	float dist = length(dir - normalize(centre)) * 260.0;
	float twinkle = 0.6 + 0.4 * sin(TIME * (1.5 + pick * 3.0) + pick * 40.0);
	vec3 colour = mix(vec3(0.75, 0.85, 1.0), vec3(1.0, 0.85, 0.7), hash31(cell + 7.0));
	return colour * smoothstep(0.32, 0.0, dist) * twinkle * star_brightness;
}

vec3 aurora_curtains(vec3 dir) {
	if (dir.y <= 0.02) {
		return vec3(0.0);
	}
	vec3 total = vec3(0.0);
	for (int layer = 0; layer < 3; layer++) {
		float lift = 0.16 + float(layer) * 0.09;
		vec2 plane = dir.xz / (dir.y + lift);
		float sweep = plane.x * 0.55 + float(layer) * 3.1 + TIME * 0.05;
		float ribbon = fbm(vec2(sweep, plane.y * 0.25 + TIME * 0.02));
		float band = smoothstep(0.07, 0.0, abs(ribbon - 0.5 + 0.12 * sin(plane.x + TIME * 0.2)));
		float streaks = 0.55 + 0.45 * value_noise(vec2(sweep * 18.0, TIME * 0.4));
		float fade = smoothstep(0.02, 0.25, dir.y) * smoothstep(0.95, 0.35, dir.y);
		vec3 colour = mix(aurora_low, aurora_high, clamp(dir.y * 1.6 + float(layer) * 0.2, 0.0, 1.0));
		total += colour * band * streaks * fade * (1.0 - float(layer) * 0.25);
	}
	return total * 1.3;
}

// One palette of the sky: zenith, horizon, the glow towards the sun, and below the horizon.
vec3 palette(vec3 dir, vec3 zenith, vec3 horizon, vec3 glow, vec3 below, float glow_width) {
	float up = dir.y;
	vec3 colour = mix(horizon, zenith, pow(smoothstep(-0.02, 0.75, up), 0.55));
	vec2 flat_dir = normalize(dir.xz + 1e-4);
	vec2 flat_sun = normalize(sun_direction.xz + 1e-4);
	float towards = pow(max(dot(flat_dir, flat_sun) * 0.5 + 0.5, 0.0), glow_width);
	colour = mix(colour, glow, towards * smoothstep(0.55, 0.0, up));
	return mix(colour, below, smoothstep(0.0, -0.3, up));
}

vec3 backdrop(vec3 dir) {
	vec3 gold = palette(dir, vec3(0.2, 0.38, 0.75), vec3(0.95, 0.72, 0.5),
		vec3(1.4, 0.72, 0.32), vec3(0.42, 0.36, 0.38), 3.0);
	vec3 twilight = palette(dir, vec3(0.08, 0.12, 0.34), vec3(0.62, 0.36, 0.46),
		vec3(1.0, 0.42, 0.24), vec3(0.16, 0.14, 0.24), 2.0);
	vec3 night = palette(dir, vec3(0.006, 0.011, 0.04), vec3(0.035, 0.065, 0.15),
		vec3(0.1, 0.07, 0.14), vec3(0.02, 0.03, 0.07), 2.0);
	vec3 colour = mix(gold, twilight, smoothstep(0.0, 0.55, dusk));
	colour = mix(colour, night, smoothstep(0.45, 1.0, dusk));
	return colour + aurora_curtains(dir) * aurora;
}

void sky() {
	vec3 dir = normalize(EYEDIR);
	if (AT_QUARTER_RES_PASS) {
		COLOR = backdrop(dir);
		ALPHA = 1.0;
	} else {
		vec3 colour = AT_CUBEMAP_PASS ? backdrop(dir) : QUARTER_RES_COLOR.rgb;
		// The sun: a disc and the haze around it, fading as it sets.
		float day = 1.0 - smoothstep(0.0, 0.7, dusk);
		float sun = max(dot(dir, normalize(sun_direction)), 0.0);
		colour += vec3(1.0, 0.62, 0.3) * (pow(sun, 900.0) * 60.0 + pow(sun, 60.0) * 0.9
			+ pow(sun, 6.0) * 0.18) * day;
		float night = smoothstep(0.45, 1.0, dusk);
		float moon = max(dot(dir, normalize(moon_direction)), 0.0);
		float disc = smoothstep(0.9993, 0.9996, moon);
		float craters = 0.82 + 0.18 * value_noise(dir.xy * 900.0);
		colour += vec3(0.86, 0.92, 1.0) * (disc * 7.0 * craters + pow(moon, 220.0) * 0.6
			+ pow(moon, 12.0) * 0.08) * night;
		if (!AT_CUBEMAP_PASS) {
			colour += stars(dir) * smoothstep(0.0, 0.2, dir.y) * smoothstep(0.55, 1.0, dusk);
		}
		COLOR = colour;
	}
}
