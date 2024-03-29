import { TinaCMS, defineConfig } from "tinacms";

// Your hosting provider likely exposes this as an environment variable
const branch =
  process.env.GITHUB_BRANCH ||
  process.env.VERCEL_GIT_COMMIT_REF ||
  process.env.HEAD ||
  "main";

export default defineConfig({
  branch: "main",

  // Get this from tina.io
  clientId: process.env.NEXT_PUBLIC_TINA_CLIENT_ID || "",
  // Get this from tina.io
  token: process.env.TINA_TOKEN || "",

  build: {
    outputFolder: "admin",
    publicFolder: "static",
  },
  media: {
    tina: {
      mediaRoot: "",
      publicFolder: "static",
    },
  },
  // Call back hook to trigger in response of post submission
  // cmsCallback: (cms) => {
  //   cms.events.subscribe('forms:submit', async (event)=> {
  //     await fetch(process.env.TINA_HOOK || "", {
  //       method: 'POST',
  //       headers: {
  //         'Content-Type': 'application/json',
  //       },
  //       // You can pass any necessary data to your serverless function
  //       body: JSON.stringify({ message: 'New post created, trigger deployment' }),
  //     });
  //   })
  //   return cms
  // },
  // See docs on content modeling for more info on how to setup new content models: https://tina.io/docs/schema/
  schema: {
    collections: [
      {
        name: "post",
        label: "Post",
        path: "content/post",
        fields: [
          {
            type: "string",
            name: "title",
            label: "Title",
            isTitle: true,
            required: true,
          },
          {
            type: "string",
            name: "author",
            label: "Author",
            required: true,
          },
          {
            type: "string",
            list: true,
            name: "keywords",
            label: "Keywords",
            searchable: true,
            required: true,
          },
          {
            type: "string",
            list: true,
            name: "tags",
            label: "Tags",
            searchable: true,
            required: true,
          },
          {
            type: "datetime",
            name: "date",
            label: "Date",
            required: true,
          },
          {
            type: "rich-text",
            name: "body",
            label: "Body",
            isBody: true,
          },
        ],
      },
    ],
  },
});
