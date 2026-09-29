import json
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
            "xaxis": {"title": "Concert Year", "dtick": 2, "gridcolor": "#1e293b", "fixedrange": True},
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

    # 2. Top Artists Bar Chart (Top 12)
    top_artists = stats["top_artists"][:12]
    art_names = [a["artist"] for a in reversed(top_artists)]
    art_counts = [a["concert_count"] for a in reversed(top_artists)]

    top_artists_chart = {
        "data": [{
            "x": art_counts,
            "y": art_names,
            "type": "bar",
            "orientation": "h",
            "marker": {
                "color": art_counts,
                "colorscale": "Purples"
            }
        }],
        "layout": {
            "title": "",
            "dragmode": False,
            "xaxis": {"title": "Times Seen Live", "gridcolor": "#1e293b", "fixedrange": True},
            "yaxis": {"automargin": True, "gridcolor": "#1e293b", "fixedrange": True}
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

    html_out = template.render(
        username=username,
        gap=gap_results,
        stats=stats,
        artist_drilldown_json=artist_drilldown_json,
        venue_map_json=venue_map_json,
        **charts
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return output_path
