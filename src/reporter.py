import json
import math
import urllib.parse
from pathlib import Path
from typing import Dict, Any

def generate_plotly_charts(stats: Dict[str, Any], album_enrichments: Dict[str, Any]) -> Dict[str, str]:
    # 1. Timeline Chart (Yearly Concerts & Songs)
    years = list(stats["yearly_concerts"].keys())
    concert_counts = [stats["yearly_concerts"][y] for y in years]
    song_counts = [stats["yearly_songs"].get(y, 0) for y in years]

    timeline_chart = {
        "data": [
            {
                "x": years,
                "y": concert_counts,
                "name": "Concerts Attended",
                "type": "bar",
                "marker": {"color": "#6366f1"}
            },
            {
                "x": years,
                "y": song_counts,
                "name": "Songs Heard",
                "type": "scatter",
                "mode": "lines+markers",
                "yaxis": "y2",
                "line": {"color": "#ec4899", "width": 3}
            }
        ],
        "layout": {
            "title": "",
            "dragmode": False,
            "margin": {"t": 35, "b": 40, "l": 50, "r": 65},
            "xaxis": {"title": "Concert Year", "tickangle": -45, "automargin": True, "gridcolor": "#1e293b", "fixedrange": True},
            "yaxis": {"title": "Concerts", "gridcolor": "#1e293b", "automargin": True, "fixedrange": True},
            "yaxis2": {
                "title": {
                    "text": "Songs Heard",
                    "standoff": 15
                },
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "automargin": True,
                "fixedrange": True
            },
            "legend": {"orientation": "h", "y": 1.15, "x": 0.25}
        }
    }

    # 2. Top Artists Packed Bubble Chart (Data-Driven Fair Cutoff & Aspect-Weighted Packing)
    all_raw_artists = stats.get("top_artists", [])
    
    bubble_x = []
    bubble_y = []
    bubble_names = []
    bubble_counts = []
    bubble_texts = []
    bubble_font_sizes = []
    bubble_hovertexts = []
    bubble_shapes = []
    
    min_x, max_x, min_y, max_y = -50.0, 50.0, -50.0, 50.0
    
    if all_raw_artists:
        sorted_all = sorted(all_raw_artists, key=lambda x: x["concert_count"], reverse=True)
        distinct_counts = sorted(list(set(a["concert_count"] for a in sorted_all)), reverse=True)
        
        target_max = 30
        absolute_max = 38
        
        top_artists = []
        selected_cutoff = distinct_counts[0]
        
        for c_thresh in distinct_counts:
            tier_artists = [a for a in sorted_all if a["concert_count"] >= c_thresh]
            count_tier = len(tier_artists)
            
            if c_thresh == 1:
                if count_tier <= target_max and len(distinct_counts) == 1:
                    selected_cutoff = 1
                    top_artists = tier_artists
                elif count_tier <= target_max and not top_artists:
                    selected_cutoff = 1
                    top_artists = tier_artists
                break
            
            if count_tier <= absolute_max:
                selected_cutoff = c_thresh
                top_artists = tier_artists
            else:
                break
                
        if not top_artists:
            if distinct_counts[0] == 1:
                top_artists = sorted_all[:target_max]
                selected_cutoff = 1
            else:
                top_artists = [a for a in sorted_all if a["concert_count"] >= distinct_counts[0]]
                selected_cutoff = distinct_counts[0]

        sorted_artists = sorted(top_artists, key=lambda x: x["concert_count"], reverse=True)
        counts = [a["concert_count"] for a in sorted_artists]
        max_c = max(counts) if counts else 1
        min_c = min(counts) if counts else 1
        gap = 2.0
        aspect_ratio = 2.15  # Matches the ~2:1 landscape aspect ratio of the card container
        
        # Color interpolation helper for default theme (#6366f1 -> #c084fc)
        def calc_theme_hex(t_val):
            # t_val from 0.0 (lowest count) to 1.0 (highest count)
            r1, g1, b1 = 99, 102, 241   # #6366f1 (Indigo)
            r2, g2, b2 = 192, 132, 252  # #c084fc (Purple/Violet)
            r = int(r1 + (r2 - r1) * t_val)
            g = int(g1 + (g2 - g1) * t_val)
            b = int(b1 + (b2 - b1) * t_val)
            return f"#{r:02x}{g:02x}{b:02x}"
        
        circles = []
        for a in sorted_artists:
            c = a["concert_count"]
            name = str(a["artist"]).strip()
            t_norm = (c - min_c) / (max_c - min_c) if (max_c > min_c) else 1.0
            if max_c == min_c:
                r = 32.0
            else:
                r = 18.0 + 32.0 * (math.sqrt(c) / math.sqrt(max_c))
            
            fill_col = calc_theme_hex(t_norm)
            words = name.split()
            
            if r >= 42:
                # Large bubble
                if len(name) <= 12:
                    txt = name
                elif len(words) == 2 and len(words[0]) <= 8 and len(words[1]) <= 8:
                    txt = f"{words[0]}<br>{words[1]}"
                elif len(name) > 13:
                    txt = name[:11] + "…"
                else:
                    txt = name
                display_text = f"{txt}<br><b>{c}</b>"
                font_size = 11
            elif r >= 30:
                # Medium-large bubble
                if len(name) <= 8:
                    txt = name
                elif len(words) == 2 and len(words[0]) <= 6 and len(words[1]) <= 6:
                    txt = f"{words[0]}<br>{words[1]}"
                elif len(name) > 9:
                    txt = name[:7] + "…"
                else:
                    txt = name
                display_text = f"{txt}<br><b>{c}</b>"
                font_size = 9
            elif r >= 23:
                # Medium bubble
                if len(name) <= 6:
                    txt = name
                elif len(name) > 6:
                    txt = name[:5] + "…"
                else:
                    txt = name
                display_text = f"{txt}<br><b>{c}</b>"
                font_size = 8
            else:
                # Small bubble: count only to prevent clipping
                display_text = f"<b>{c}</b>"
                font_size = 8
            
            circles.append({
                "artist": name,
                "count": c,
                "r": r,
                "x": 0.0,
                "y": 0.0,
                "fill_color": fill_col,
                "display_text": display_text,
                "font_size": font_size
            })
            
        placed = []
        def dist(x1, y1, x2, y2):
            return math.hypot(x1 - x2, y1 - y2)
        def is_valid_pos(cx, cy, cr, placed_circles):
            for p in placed_circles:
                if dist(cx, cy, p["x"], p["y"]) < (cr + p["r"] + gap - 1e-4):
                    return False
            return True

        for i, c in enumerate(circles):
            r = c["r"]
            if i == 0:
                c["x"] = 0.0
                c["y"] = 0.0
                placed.append(c)
            elif i == 1:
                c["x"] = placed[0]["r"] + r + gap
                c["y"] = 0.0
                placed.append(c)
            else:
                best_pos = None
                best_dist = float("inf")
                for j in range(len(placed)):
                    for k in range(j + 1, len(placed)):
                        c1, c2 = placed[j], placed[k]
                        d1, d2 = c1["r"] + r + gap, c2["r"] + r + gap
                        dx, dy = c2["x"] - c1["x"], c2["y"] - c1["y"]
                        d = math.hypot(dx, dy)
                        if d > (d1 + d2) or d < abs(d1 - d2) or d == 0:
                            continue
                        a_dist = (d1*d1 - d2*d2 + d*d) / (2.0 * d)
                        h2 = d1*d1 - a_dist*a_dist
                        if h2 < 0:
                            continue
                        h_dist = math.sqrt(h2)
                        x2 = c1["x"] + (dx * a_dist) / d
                        y2 = c1["y"] + (dy * a_dist) / d
                        candidates = [
                            (x2 + (dy * h_dist) / d, y2 - (dx * h_dist) / d),
                            (x2 - (dy * h_dist) / d, y2 + (dx * h_dist) / d)
                        ]
                        for cand_x, cand_y in candidates:
                            if is_valid_pos(cand_x, cand_y, r, placed):
                                # Aspect-weighted distance so circles fill landscape space
                                d_orig = math.hypot(cand_x / aspect_ratio, cand_y)
                                if d_orig < best_dist:
                                    best_dist = d_orig
                                    best_pos = (cand_x, cand_y)
                if best_pos is None:
                    angle = 0.0
                    while angle < 100.0:
                        rad = 1.0 * angle
                        cand_x = rad * aspect_ratio * math.cos(angle)
                        cand_y = rad * math.sin(angle)
                        if is_valid_pos(cand_x, cand_y, r, placed):
                            best_pos = (cand_x, cand_y)
                            break
                        angle += 0.05
                if best_pos:
                    c["x"] = best_pos[0]
                    c["y"] = best_pos[1]
                else:
                    c["x"] = (r + 50) * i * aspect_ratio
                    c["y"] = 0.0
                placed.append(c)
                
        min_x = min(c["x"] - c["r"] for c in placed)
        max_x = max(c["x"] + c["r"] for c in placed)
        min_y = min(c["y"] - c["r"] for c in placed)
        max_y = max(c["y"] + c["r"] for c in placed)
        
        bubble_x = [round(c["x"], 2) for c in placed]
        bubble_y = [round(c["y"], 2) for c in placed]
        bubble_names = [c["artist"] for c in placed]
        bubble_counts = [c["count"] for c in placed]
        bubble_texts = [c["display_text"] for c in placed]
        bubble_font_sizes = [c["font_size"] for c in placed]
        bubble_hovertexts = [f"<b>{c['artist']}</b><br>{c['count']} shows attended" for c in placed]
        
        for c in placed:
            bubble_shapes.append({
                "type": "circle",
                "xref": "x",
                "yref": "y",
                "x0": round(c["x"] - c["r"], 2),
                "y0": round(c["y"] - c["r"], 2),
                "x1": round(c["x"] + c["r"], 2),
                "y1": round(c["y"] + c["r"], 2),
                "fillcolor": c["fill_color"],
                "line": {
                    "color": "rgba(255, 255, 255, 0.3)",
                    "width": 1.5
                },
                "layer": "below"
            })

    pad = 8.0
    top_artists_chart = {
        "data": [{
            "x": bubble_x,
            "y": bubble_y,
            "text": bubble_texts,
            "customdata": bubble_names,
            "type": "scatter",
            "mode": "markers+text",
            "marker": {
                "size": [max(20, min(80, int(fs * 4))) for fs in bubble_font_sizes] if bubble_font_sizes else [],
                "color": "rgba(0,0,0,0.001)",
                "opacity": 0.01,
                "showscale": False
            },
            "textposition": "middle center",
            "textfont": {
                "family": "Inter, sans-serif",
                "size": bubble_font_sizes,
                "color": "#ffffff"
            },
            "hoverinfo": "text",
            "hovertext": bubble_hovertexts
        }],
        "layout": {
            "title": "",
            "dragmode": False,
            "hovermode": "closest",
            "shapes": bubble_shapes,
            "margin": {"l": 10, "r": 10, "t": 10, "b": 10},
            "xaxis": {
                "visible": False,
                "showgrid": False,
                "zeroline": False,
                "showticklabels": False,
                "range": [round(min_x - pad, 2), round(max_x + pad, 2)],
                "fixedrange": True
            },
            "yaxis": {
                "visible": False,
                "showgrid": False,
                "zeroline": False,
                "showticklabels": False,
                "range": [round(min_y - pad, 2), round(max_y + pad, 2)],
                "scaleanchor": "x",
                "scaleratio": 1,
                "fixedrange": True
            },
            "plot_bgcolor": "rgba(0,0,0,0)",
            "paper_bgcolor": "rgba(0,0,0,0)"
        }
    }

    # 3. Treemap (Artist -> Album -> Track) with explicit IDs and metadata
    ids = ["root"]
    labels = ["All Live Music"]
    parents = [""]
    values = [0]
    custom_data = [{"type": "root"}]

    top_artist_names = {a["artist"] for a in stats.get("top_artists", [])}
    artist_tracks = stats.get("all_songs_list", [])

    tree_map_counts = {}
    for item in artist_tracks:
        art = item.get("artist")
        if not art:
            continue
        if top_artist_names and art not in top_artist_names:
            continue
        song = item["song"]
        is_cov = item.get("is_cover")
        info = album_enrichments.get(f"{art}_{song}".lower(), {})
        album = info.get("album", "Non-Album / Singles")
        if (is_cov or info.get("is_cover")) and album == "Non-Album / Singles":
            album = "Covers"
        yr = info.get("release_year")
        album_label = f"{album} ({yr})" if yr and album != "Covers" else album

        if art not in tree_map_counts:
            tree_map_counts[art] = {}
        if album_label not in tree_map_counts[art]:
            tree_map_counts[art][album_label] = {}
        tree_map_counts[art][album_label][song] = tree_map_counts[art][album_label].get(song, 0) + 1

    total_root_val = 0
    for art, albums in tree_map_counts.items():
        art_id = f"art_{art}"
        art_total = sum(sum(songs.values()) for songs in albums.values())
        total_root_val += art_total

        ids.append(art_id)
        labels.append(art)
        parents.append("root")
        values.append(art_total)
        custom_data.append({"type": "artist", "artist": art})

        for album_label, songs in albums.items():
            album_id = f"alb_{art}_{album_label}"
            album_total = sum(songs.values())

            ids.append(album_id)
            labels.append(album_label)
            parents.append(art_id)
            values.append(album_total)
            custom_data.append({"type": "album", "artist": art, "album": album_label})

            for song, cnt in songs.items():
                song_id = f"trk_{art}_{album_label}_{song}"
                ids.append(song_id)
                labels.append(song)
                parents.append(album_id)
                values.append(cnt)
                custom_data.append({"type": "song", "artist": art, "album": album_label, "song": song})

    values[0] = total_root_val or 1

    treemap_chart = {
        "data": [{
            "type": "treemap",
            "ids": ids,
            "labels": labels,
            "parents": parents,
            "values": values,
            "customdata": custom_data,
            "textinfo": "label+value",
            "branchvalues": "remainder",
            "pathbar": {
                "visible": True,
                "thickness": 28,
                "textfont": {"size": 12, "color": "#ffffff"},
                "side": "top"
            },
            "marker": {
                "colorscale": "Sunsetdark",
                "line": {"width": 1.5, "color": "#0f172a"}
            },
            "hovertemplate": "<b>%{label}</b><br>Plays heard: %{value}<br><i>Click to zoom or open drilldown</i><extra></extra>"
        }],
        "layout": {
            "dragmode": False,
            "margin": {"t": 35, "b": 10, "l": 10, "r": 10}
        }
    }

    # 4. Sunburst of Top Artists Share
    sb_labels = [a["artist"] for a in stats["top_artists"][:8]]
    sb_values = [a["concert_count"] for a in stats["top_artists"][:8]]
    sunburst_chart = {
        "data": [{
            "type": "pie",
            "labels": sb_labels,
            "values": sb_values,
            "hole": 0.4,
            "textinfo": "label+percent",
            "marker": {"colorscale": "Magma"}
        }],
        "layout": {
            "dragmode": False,
            "showlegend": False,
            "margin": {"t": 10, "b": 10, "l": 10, "r": 10}
        }
    }

    # 5. Song Release Era / Year Distribution (Histogram of Album Release Years)
    release_years = []
    song_ages = []
    for item in stats["all_songs_list"]:
        art = item["artist"]
        song = item["song"]
        concert_year = item.get("year")
        info = album_enrichments.get(f"{art}_{song}".lower(), {})
        rel_year = info.get("release_year")
        if rel_year and 1960 <= rel_year <= 2030:
            release_years.append(rel_year)
            if concert_year and concert_year >= rel_year:
                song_ages.append(concert_year - rel_year)

    era_chart = {
        "data": [{
            "x": release_years,
            "type": "histogram",
            "xbins": {"size": 2},
            "marker": {
                "color": "#10b981",
                "line": {"color": "#064e3b", "width": 1}
            }
        }],
        "layout": {
            "title": "",
            "dragmode": False,
            "xaxis": {"title": "Album / Song Release Year", "gridcolor": "#1e293b", "fixedrange": True},
            "yaxis": {"title": "Songs Heard Live", "gridcolor": "#1e293b", "fixedrange": True}
        }
    }

    # 6. Song Age at Performance Time
    song_age_chart = {
        "data": [{
            "x": song_ages,
            "type": "histogram",
            "xbins": {"size": 2},
            "marker": {
                "color": "#8b5cf6",
                "line": {"color": "#4c1d95", "width": 1}
            }
        }],
        "layout": {
            "title": "",
            "dragmode": False,
            "xaxis": {"title": "Song Age when Performed (Years Since Release)", "gridcolor": "#1e293b", "fixedrange": True},
            "yaxis": {"title": "Occurrences", "gridcolor": "#1e293b", "fixedrange": True}
        }
    }

    return {
        "plotly_timeline": json.dumps(timeline_chart),
        "plotly_top_artists": json.dumps(top_artists_chart),
        "plotly_treemap": json.dumps(treemap_chart),
        "plotly_sunburst": json.dumps(sunburst_chart),
        "plotly_era": json.dumps(era_chart),
        "plotly_song_age": json.dumps(song_age_chart)
    }

def render_html_report(template_dir: Path, output_path: Path, username: str,
                       gap_results: Dict[str, Any], stats: Dict[str, Any],
                       album_enrichments: Dict[str, Any]) -> Path:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("dashboard.html")

    charts = generate_plotly_charts(stats, album_enrichments)

    # Attach album information to each song in artist_drilldown
    drilldown = stats.get("artist_drilldown", {})
    for artist_name, art_data in drilldown.items():
        albums_set = set()
        for s in art_data.get("songs", []):
            song_name = s.get("song")
            key = f"{artist_name}_{song_name}".lower()
            info = album_enrichments.get(key, {})
            album_title = info.get("album", "Non-Album / Singles")
            # Check if any occurrence was a cover or info is_cover
            is_cov = any(o.get("is_cover") for o in s.get("occurrences", [])) or info.get("is_cover")
            if is_cov and album_title == "Non-Album / Singles":
                album_title = "Covers"
            rel_year = info.get("release_year") if album_title != "Covers" else None
            s["album"] = album_title
            s["release_year"] = rel_year
            s["album_display"] = f"{album_title} ({rel_year})" if rel_year else album_title
            albums_set.add(album_title)
        art_data["albums_list"] = sorted(list(albums_set))

    artist_drilldown_json = json.dumps(drilldown)
    venue_map_json = json.dumps(stats.get("venue_map", {}))
    musicians_json = json.dumps(stats.get("musicians", {}).get("top_musicians", []))

    html_out = template.render(
        username=username,
        gap=gap_results,
        stats=stats,
        artist_drilldown_json=artist_drilldown_json,
        venue_map_json=venue_map_json,
        musicians_json=musicians_json,
        **charts
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return output_path
