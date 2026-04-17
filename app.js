const query = `
  SELECT 
    t.id,
    t.accepted_scientific_name AS name,
    COALESCE(i.image_url, g.image_url) AS image_url
  FROM public.orchid_taxonomy t

  LEFT JOIN LATERAL (
    SELECT image_url
    FROM public.orchid_images
    WHERE taxonomy_id = t.id
      AND image_url IS NOT NULL
      AND (
        LOWER(image_url) LIKE '%flower%' OR
        LOWER(image_url) LIKE '%bloom%' OR
        LOWER(image_url) LIKE '%orchid%'
      )
    ORDER BY RANDOM()
    LIMIT 1
  ) i ON TRUE

  LEFT JOIN LATERAL (
    SELECT image_url
    FROM public.oc_gbif_orchid_images
    WHERE LOWER(scientific_name) = LOWER(t.accepted_scientific_name)
      AND image_url IS NOT NULL
    ORDER BY RANDOM()
    LIMIT 1
  ) g ON TRUE

  WHERE COALESCE(i.image_url, g.image_url) IS NOT NULL

  ORDER BY RANDOM()
  LIMIT $1;
`;

const { rows } = await pool.query(query, [limit]);

res.json(rows);
